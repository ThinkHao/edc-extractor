from __future__ import annotations

from datetime import datetime
from time import monotonic

from .scheduler_store import SchedulerStore
from .sync_engine import SyncEngine


def run_sync_job(
    store: SchedulerStore,
    engine: SyncEngine,
    execution_id: int,
    start_time: datetime,
    end_time: datetime,
    source_host: str,
    target_host: str,
) -> None:
    def progress_callback(completed_chunks, total_chunks, chunk_summary, summary):
        store.update_progress(
            execution_id,
            {
                "total_chunks": total_chunks,
                "completed_chunks": completed_chunks,
                "percent": int((completed_chunks / total_chunks) * 100) if total_chunks else 0,
                "current_start_time": chunk_summary.start_time.isoformat(sep=" "),
                "current_end_time": chunk_summary.end_time.isoformat(sep=" "),
                "rows_read": summary.rows_read,
                "rows_written": summary.rows_written,
                "unmapped_count": summary.unmapped_count,
                "negative_service_count": summary.negative_service_count,
                "negative_cache_count": summary.negative_cache_count,
                "duration_ms": summary.duration_ms,
            },
        )

    try:
        summary = engine.sync(start_time, end_time, progress_callback=progress_callback)
        store.complete_execution(execution_id, summary)
    except Exception as exc:
        error_message = describe_sync_error(exc, source_host, target_host)
        store.fail_execution(execution_id, error_message)


def run_metadata_sync_job(
    store: SchedulerStore,
    target,
    execution_id: int,
    entity_ids: list[int],
) -> None:
    started = monotonic()

    def progress_callback(progress: dict) -> None:
        payload = dict(progress)
        payload["duration_ms"] = int((monotonic() - started) * 1000)
        store.update_progress(
            execution_id,
            {
                "total_chunks": payload.get("total_entities", 0),
                "completed_chunks": payload.get("completed_entities", 0),
                "percent": payload.get("percent", 0),
                "rows_read": payload.get("rows_scanned", 0),
                "rows_written": payload.get("rows_updated", 0),
                **payload,
            },
        )

    try:
        summary = target.reconcile_traffic_metadata(entity_ids, progress_callback=progress_callback)
        summary["duration_ms"] = int((monotonic() - started) * 1000)
        store.complete_custom_execution(execution_id, summary)
    except Exception as exc:
        store.fail_execution(execution_id, f"EDC 历史元数据同步失败：{exc}")


def describe_sync_error(exc: Exception, source_host: str, target_host: str) -> str:
    raw = str(exc)
    if source_host and source_host in raw:
        return f"源库连接或查询失败：{raw}"
    if target_host and target_host in raw:
        return f"目标库连接或写入失败：{raw}"
    return raw
