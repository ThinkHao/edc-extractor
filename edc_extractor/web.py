from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from .config import load_config
from .entity_onboarding import ENTITY_TYPES, build_entity_payload
from .mysql_adapters import MySQLEDCSource, MySQLEDCTarget
from .notification import FeishuNotificationClient
from .onboarding import EDCOnboardingMonitor
from .scheduler_store import SchedulerStore
from .sync_jobs import describe_sync_error, run_metadata_sync_job, run_sync_job
from .sync_engine import SyncEngine
from .task_scheduler import EDCTaskScheduler, validate_cron_expression

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"


def create_app(config_path: str | None = None, start_scheduler: bool | None = None) -> Flask:
    app = Flask(__name__)
    config = load_config(config_path or os.environ.get("EDC_EXTRACTOR_CONFIG", "config.ini"))
    scheduler_db = Path(os.environ.get("EDC_SCHEDULER_DB", Path(__file__).resolve().parent / "scheduler.db"))
    store = SchedulerStore(scheduler_db)
    source = MySQLEDCSource(config.source)
    target = MySQLEDCTarget(config.target)
    engine = SyncEngine(source, target, config.sync)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="edc-sync")
    onboarding_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="edc-onboarding")
    backfill_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="edc-backfill")
    metadata_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="edc-metadata")
    notifier = FeishuNotificationClient(config.notification)
    onboarding = EDCOnboardingMonitor(
        source=source,
        target=target,
        engine=engine,
        notifier=notifier,
        backfill_executor=backfill_executor,
        sync_config=config.sync,
    )
    task_scheduler = EDCTaskScheduler(
        store=store,
        engine=engine,
        config=config.scheduler,
        source_host=config.source.host,
        target_host=config.target.host,
        executor=executor,
        onboarding_cycle=onboarding.run_cycle,
    )
    app.extensions["edc_task_scheduler"] = task_scheduler
    app.extensions["edc_onboarding"] = onboarding
    app.extensions["edc_notifier"] = notifier
    task_scheduler.ensure_default_task()
    if start_scheduler is None:
        start_scheduler = os.environ.get("EDC_DISABLE_SCHEDULER") != "1" and (
            not config.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"
        )
    if start_scheduler:
        task_scheduler.start()

    @app.get("/")
    def index():
        return _serve_frontend_index()

    @app.get("/assets/<path:filename>")
    def frontend_assets(filename: str):
        assets_dir = FRONTEND_DIST_DIR / "assets"
        if not assets_dir.exists():
            return _frontend_not_built()
        return send_from_directory(assets_dir, filename)

    @app.get("/<path:path>")
    def frontend_routes(path: str):
        if path.startswith("api/"):
            return jsonify({"error": "not found"}), 404
        asset_path = FRONTEND_DIST_DIR / path
        if asset_path.is_file():
            return send_from_directory(FRONTEND_DIST_DIR, path)
        return _serve_frontend_index()

    @app.get("/health")
    def health():
        return jsonify(build_health_payload(source, target, config))

    @app.get("/api/executions")
    def executions():
        return jsonify({"items": build_executions_payload(store)})

    @app.get("/api/executions/<int:execution_id>")
    def execution(execution_id: int):
        item = store.get_execution(execution_id)
        if not item:
            return jsonify({"error": "execution not found"}), 404
        return jsonify(item)

    @app.get("/api/onboarding")
    def onboarding_state():
        try:
            return jsonify(build_onboarding_payload(target))
        except Exception as exc:
            return jsonify({"error": f"获取 EDC 录入状态失败: {exc}"}), 500

    @app.post("/api/onboarding/<int:candidate_id>/retry")
    def retry_onboarding(candidate_id: int):
        try:
            if not target.reset_candidate_for_retry(candidate_id):
                return jsonify({"error": "候选记录不存在，或当前状态不可重试"}), 409
            scheduled = onboarding.process_pending()
            return jsonify({"candidate_id": candidate_id, "backfills_scheduled": scheduled})
        except Exception as exc:
            return jsonify({"error": f"重试 EDC 补录失败: {exc}"}), 500

    @app.get("/api/tasks")
    def tasks():
        return jsonify({"items": build_tasks_payload(store, task_scheduler)})

    @app.get("/api/events")
    def events():
        stream = _parse_bool(request.args.get("stream"), default=False)
        once = _parse_bool(request.args.get("once"), default=not stream)

        def generate():
            yield _sse_event("snapshot", build_snapshot_payload(store, task_scheduler, source, target, config))
            if once:
                return

            ticks = 0
            while True:
                sleep(1)
                ticks += 1
                try:
                    yield _sse_event("active_execution", build_active_execution_payload(store))
                    if ticks % 5 == 0:
                        yield _sse_event("tasks", {"items": build_tasks_payload(store, task_scheduler)})
                        yield _sse_event("executions", {"items": build_executions_payload(store)})
                        yield _sse_event("onboarding", build_onboarding_payload(target))
                    if ticks % 30 == 0:
                        yield _sse_event("health", build_health_payload(source, target, config))
                    if ticks % 15 == 0:
                        yield _sse_event("heartbeat", {"server_time": _server_time()})
                except Exception as exc:
                    yield _sse_event("error", {"message": str(exc), "server_time": _server_time()})

        response = Response(stream_with_context(generate()), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    @app.patch("/api/tasks/<int:task_id>")
    def update_task(task_id: int):
        payload = request.get_json(silent=True) or {}
        task = store.get_scheduled_task(task_id)
        if not task:
            return jsonify({"error": "scheduled task not found"}), 404
        try:
            cron_expression = validate_cron_expression(str(payload.get("cron_expression", task["cron_expression"])))
            time_window_minutes = _parse_positive_int(
                payload.get("time_window_minutes", task["time_window_minutes"]),
                "time_window_minutes",
            )
            delay_minutes = _parse_positive_int(payload.get("delay_minutes", task["delay_minutes"]), "delay_minutes")
            enabled = _parse_bool(payload.get("enabled"), default=bool(task["enabled"]))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        updated = store.update_scheduled_task(
            task_id,
            cron_expression=cron_expression,
            time_window_minutes=time_window_minutes,
            delay_minutes=delay_minutes,
            enabled=enabled,
        )
        task_scheduler.reload_jobs()
        return jsonify(_serialize_task(updated, task_scheduler))

    @app.post("/api/tasks/<int:task_id>/run")
    def run_task(task_id: int):
        if not store.get_scheduled_task(task_id):
            return jsonify({"error": "scheduled task not found"}), 404
        execution_id = task_scheduler.submit_task(task_id)
        if execution_id is None:
            return jsonify({"error": "任务未启用，或当前已有同步任务正在执行"}), 409
        return jsonify({"execution_id": execution_id, "status": "running"}), 202

    @app.get("/api/source/entities")
    def source_entities():
        try:
            start_time = _parse_time(request.args.get("start_time"))
            end_time = _parse_time(request.args.get("end_time"))
            limit = _parse_limit(request.args.get("limit"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        only_unconfigured = _parse_bool(request.args.get("only_unconfigured"), default=False)
        try:
            candidates = source.discover_entity_candidates(start_time, end_time, limit)
            configured_entities = target.load_entity_mappings()
            upsert_candidates = getattr(target, "upsert_entity_candidates", None)
            if callable(upsert_candidates):
                upsert_candidates(candidates, configured_keys=set(configured_entities))
            candidate_states = {}
            state_reader = getattr(target, "list_entity_candidate_states", None)
            if callable(state_reader) and candidates:
                candidate_states = state_reader(
                    {(candidate.edc_name, candidate.sn or "") for candidate in candidates}
                )
        except Exception as exc:
            return jsonify({"error": f"源端 EDC 发现失败: {exc}"}), 500
        payload = build_entity_payload(
            candidates,
            configured_entities,
            include_configured_history=not only_unconfigured,
            candidate_states=candidate_states,
        )
        if only_unconfigured:
            payload = [item for item in payload if not item["configured"]]
        return jsonify({"items": payload})

    @app.post("/api/entities")
    def create_entities():
        payload = request.get_json(silent=True) or {}
        rows = payload.get("items")
        if not isinstance(rows, list):
            return jsonify({"error": "items must be a list"}), 400
        try:
            _validate_entity_rows(rows)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        upserted = target.upsert_entities(rows)
        entity_keys = {
            (str(row["edc_name"]).strip(), str(row.get("sn") or "").strip())
            for row in rows
        }

        metadata_execution_id = None
        try:
            entity_ids = target.entity_ids_for_keys(entity_keys)
            if entity_ids:
                start_time, end_time = target.get_entity_traffic_bounds(entity_ids)
                now = datetime.now()
                start_time = start_time or now
                end_time = end_time or (start_time + timedelta(minutes=5))
                if end_time <= start_time:
                    end_time = start_time + timedelta(minutes=5)
                metadata_execution_id = store.create_execution(
                    None,
                    start_time,
                    end_time,
                    kind="metadata_reconcile",
                )
                metadata_executor.submit(
                    run_metadata_sync_job,
                    store,
                    target,
                    metadata_execution_id,
                    entity_ids,
                )
        except Exception:
            app.logger.exception("写入映射后触发历史元数据同步失败")

        def schedule_backfill() -> None:
            try:
                onboarding.process_pending(entity_keys=entity_keys)
            except Exception:
                app.logger.exception("写入映射后触发历史补录失败")

        onboarding_executor.submit(schedule_backfill)
        return jsonify(
            {
                "upserted": upserted,
                "backfill_status": "scheduled",
                "metadata_sync_status": "scheduled" if metadata_execution_id else "not_scheduled",
                "metadata_sync_execution_id": metadata_execution_id,
            }
        ), 202

    @app.get("/api/entities/configured")
    def configured_entities():
        try:
            items = target.list_entity_records()
            return jsonify({"items": [_serialize_entity(item) for item in items]})
        except Exception as exc:
            return jsonify({"error": f"获取实体状态失败: {exc}"}), 500

    @app.patch("/api/entities/<int:entity_id>/enabled")
    def update_entity_enabled(entity_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            enabled = _parse_strict_bool(payload.get("enabled"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            item = target.set_entity_enabled(
                entity_id,
                enabled,
                operator="web",
                source_ip=request.remote_addr,
            )
            if item is None:
                return jsonify({"error": "实体不存在"}), 404
            return jsonify({"item": _serialize_entity(item)})
        except Exception as exc:
            return jsonify({"error": f"更新实体状态失败: {exc}"}), 500

    @app.patch("/api/entity-candidates/<int:candidate_id>/enabled")
    def update_entity_candidate_enabled(candidate_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            enabled = _parse_strict_bool(payload.get("enabled"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        set_candidate_enabled = getattr(target, "set_entity_candidate_enabled", None)
        if not callable(set_candidate_enabled):
            return jsonify({"error": "当前目标库不支持待录入条目状态管理"}), 501
        try:
            item = set_candidate_enabled(
                candidate_id,
                enabled,
                operator="web",
                source_ip=request.remote_addr,
            )
            if item is None:
                return jsonify({"error": "待录入条目不存在"}), 404
            return jsonify({"item": _serialize_candidate(item)})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        except Exception as exc:
            return jsonify({"error": f"更新待录入条目状态失败: {exc}"}), 500

    @app.post("/api/sync")
    def sync():
        payload = request.get_json(silent=True) or {}
        start_time = _parse_time(payload.get("start_time"))
        end_time = _parse_time(payload.get("end_time"))
        execution_id = store.create_execution(None, start_time, end_time)
        executor.submit(run_sync_job, store, engine, execution_id, start_time, end_time, config.source.host, config.target.host)
        return jsonify({"execution_id": execution_id, "status": "running"}), 202

    return app


def _disabled_source_names(configured_entities) -> set[str]:
    """隐藏仅由停用映射覆盖的源端名称，保留同名仍有启用 SN 的实体。"""
    enabled_names = {
        key[0] for key, mapping in configured_entities.items() if mapping.enabled
    }
    disabled_names = {
        key[0] for key, mapping in configured_entities.items() if not mapping.enabled
    }
    return disabled_names - enabled_names


def _parse_time(value) -> datetime:
    if not value:
        raise ValueError("start_time and end_time are required")
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return datetime.fromisoformat(raw)


def _parse_limit(value) -> int:
    if value is None:
        return 500
    limit = int(value)
    if limit <= 0 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")
    return limit


def _parse_positive_int(value, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return parsed


def _parse_bool(value, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_strict_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("enabled must be a boolean")


def _validate_entity_rows(rows: list[dict]) -> None:
    required = ("edc_name", "display_name", "region", "cp")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"items[{index}] must be an object")
        missing = [field for field in required if not str(row.get(field) or "").strip()]
        if missing:
            raise ValueError(f"items[{index}] missing required fields: {', '.join(missing)}")
        entity_type = str(row.get("entity_type") or "").strip()
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"items[{index}].entity_type must be one of: {', '.join(ENTITY_TYPES)}")


def build_snapshot_payload(
    store: SchedulerStore,
    task_scheduler: EDCTaskScheduler,
    source,
    target,
    config,
) -> dict:
    return {
        "tasks": build_tasks_payload(store, task_scheduler),
        "executions": build_executions_payload(store),
        "active_execution": build_active_execution_payload(store),
        "health": build_health_payload(source, target, config),
        "onboarding": build_onboarding_payload(target),
        "server_time": _server_time(),
    }


def build_tasks_payload(store: SchedulerStore, task_scheduler: EDCTaskScheduler) -> list[dict]:
    return [_serialize_task(task, task_scheduler) for task in store.list_scheduled_tasks()]


def build_executions_payload(store: SchedulerStore) -> list[dict]:
    return store.list_executions()


def build_onboarding_payload(target) -> dict:
    list_candidates = getattr(target, "list_entity_candidates", None)
    if not callable(list_candidates):
        return {"items": [], "counts": {}}
    items = list_candidates(limit=5000)
    serialized = [_serialize_candidate(item) for item in items]
    counts: dict[str, int] = {}
    for item in serialized:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {"items": serialized, "counts": counts}


def _serialize_candidate(item: dict) -> dict:
    out = dict(item)
    for key in (
        "first_seen_at",
        "latest_seen_at",
        "last_notified_at",
        "backfill_start_at",
        "backfill_end_at",
        "discovered_at",
        "confirmed_at",
        "backfill_completed_at",
        "updated_at",
    ):
        value = out.get(key)
        if isinstance(value, datetime):
            out[key] = value.isoformat(sep=" ")
    out["enabled"] = bool(out.get("enabled", True))
    return out


def _serialize_entity(item: dict) -> dict:
    out = dict(item)
    for key in ("created_at", "updated_at"):
        value = out.get(key)
        if isinstance(value, datetime):
            out[key] = value.isoformat(sep=" ")
    out["enabled"] = bool(out.get("enabled"))
    out["is_backup"] = bool(out.get("is_backup"))
    return out


def build_active_execution_payload(store: SchedulerStore) -> dict | None:
    for item in store.list_executions():
        if item.get("status") == "running":
            return item
    return None


def build_health_payload(source, target, config) -> dict:
    source_status = _check_database("source", source, config.source.database)
    target_status = _check_database("target", target, config.target.database)
    recommended_source_index = False
    source_index_error = None
    if source_status["connected"]:
        try:
            recommended_source_index = source.has_recommended_index()
        except Exception as exc:
            source_index_error = str(exc)

    status = "ok"
    if not source_status["connected"] or not target_status["connected"] or not recommended_source_index:
        status = "degraded"

    return {
        "status": status,
        "recommended_source_index": recommended_source_index,
        "source": {**source_status, "recommended_index": recommended_source_index, "index_error": source_index_error},
        "target": target_status,
    }


def _check_database(role: str, adapter, database: str) -> dict:
    try:
        adapter.ping()
        return {"role": role, "connected": True, "database": database, "error": None}
    except Exception as exc:
        return {"role": role, "connected": False, "database": database, "error": str(exc)}


def _sse_event(event: str, payload: dict | None) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {data}\n\n"


def _server_time() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _serialize_task(task: dict | None, task_scheduler: EDCTaskScheduler) -> dict:
    if not task:
        return {}
    payload = dict(task)
    payload["enabled"] = bool(payload["enabled"])
    payload.update(task_scheduler.job_state(int(payload["id"])))
    return payload


def _describe_sync_error(exc: Exception, source_host: str, target_host: str) -> str:
    return describe_sync_error(exc, source_host, target_host)


def _serve_frontend_index():
    index_path = FRONTEND_DIST_DIR / "index.html"
    if not index_path.exists():
        return _frontend_not_built()
    return send_from_directory(FRONTEND_DIST_DIR, "index.html")


def _frontend_not_built():
    return Response(
        """
        <!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8">
          <title>EDC Extractor</title>
          <style>
            body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 48px; color: #1f2937; }
            code { background: #f3f4f6; padding: 2px 6px; border-radius: 4px; }
          </style>
        </head>
        <body>
          <h1>前端尚未构建</h1>
          <p>请先在项目根目录执行 <code>npm --prefix frontend install</code> 和 <code>npm --prefix frontend run build</code>，然后刷新页面。</p>
        </body>
        </html>
        """,
        status=503,
        mimetype="text/html",
    )


def main() -> None:
    config = load_config(os.environ.get("EDC_EXTRACTOR_CONFIG", "config.ini"))
    app = create_app(os.environ.get("EDC_EXTRACTOR_CONFIG", "config.ini"))
    app.run(host=config.app_host, port=config.app_port, debug=config.debug)


if __name__ == "__main__":
    main()
