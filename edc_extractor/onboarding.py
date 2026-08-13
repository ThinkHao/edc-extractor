from __future__ import annotations

import logging
from concurrent.futures import Executor
from datetime import datetime, timedelta

from .config import SyncConfig
from .entity_onboarding import SourceEntityCandidate
from .notification import NotificationClient
from .sync_engine import SyncEngine, build_time_chunks

LOGGER = logging.getLogger(__name__)


class EDCOnboardingMonitor:
    """发现未登记 EDC、提醒人工录入，并在录入后按源端历史窗口补录。"""

    def __init__(
        self,
        *,
        source,
        target,
        engine: SyncEngine,
        notifier: NotificationClient,
        backfill_executor: Executor,
        sync_config: SyncConfig,
    ):
        self.source = source
        self.target = target
        self.engine = engine
        self.notifier = notifier
        self.backfill_executor = backfill_executor
        self.sync_config = sync_config

    def run_cycle(self, now: datetime | None = None) -> dict:
        current = now or datetime.now()
        candidates = self.source.discover_entity_candidates(
            current - timedelta(hours=24), current, limit=5000
        )
        mappings = self.target.load_entity_mappings()
        self.target.upsert_entity_candidates(candidates, configured_keys=set(mappings))
        scheduled = self.process_pending()
        notified = self.notify_pending(current)
        return {"discovered": len(candidates), "backfills_scheduled": scheduled, "notifications_sent": notified}

    def process_pending(self) -> int:
        pending = self.target.list_entity_candidates(statuses={"pending"}, limit=5000)
        entities = {
            (entity.edc_name, entity.sn or ""): entity
            for entity in self.target.load_enabled_entities()
        }
        scheduled = 0
        for candidate in pending:
            key = (str(candidate["edc_name"]), str(candidate.get("sn") or ""))
            entity = entities.get(key)
            if entity is None:
                continue
            first_seen, latest_seen = self.source.get_entity_time_bounds(*key)
            first_seen = first_seen or candidate.get("first_seen_at")
            latest_seen = latest_seen or candidate.get("latest_seen_at")
            if not first_seen or not latest_seen:
                self.target.fail_candidate_backfill(int(candidate["id"]), "源端未找到可补录的时间范围")
                continue
            end_time = min(_ceil_5m(latest_seen + timedelta(minutes=5)), _ceil_5m(datetime.now()))
            if end_time <= first_seen:
                self.target.complete_candidate_backfill(int(candidate["id"]), 0)
                continue
            if self.target.mark_candidate_backfill_pending(
                int(candidate["id"]), entity.id, first_seen, end_time
            ):
                self.backfill_executor.submit(
                    self._run_backfill,
                    int(candidate["id"]),
                    key,
                    first_seen,
                    end_time,
                )
                scheduled += 1
        return scheduled

    def notify_pending(self, now: datetime | None = None) -> int:
        if not self.notifier.enabled:
            return 0
        current = now or datetime.now()
        sent = 0
        for candidate in self.target.list_entity_candidates(statuses={"pending", "failed"}, limit=5000):
            level = 3 if candidate.get("status") == "failed" else _notification_level(candidate, current)
            if level <= int(candidate.get("last_notified_level") or 0):
                continue
            title = _notification_title(level)
            text = (
                f"EDC：{candidate['edc_name']}\n"
                f"SN：{candidate.get('sn') or '-'}\n"
                f"首次发现：{candidate.get('first_seen_at') or '-'}\n"
                f"最近数据：{candidate.get('latest_seen_at') or '-'}\n"
                f"源端记录数：{candidate.get('record_count') or 0}\n"
            )
            if candidate.get("status") == "failed":
                text += f"补录错误：{candidate.get('backfill_error')}\n"
            text += "请在 EDC 提取管理台确认映射；确认后系统会自动补录历史流量。"
            if self.notifier.send(title, text, current):
                self.target.record_candidate_notification(int(candidate["id"]), level, current)
                sent += 1
        return sent

    def _run_backfill(
        self,
        candidate_id: int,
        entity_key: tuple[str, str],
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        if not self.target.claim_candidate_backfill(candidate_id):
            return
        rows_written = 0
        try:
            for chunk_start, chunk_end in build_time_chunks(
                start_time, end_time, max(1, self.sync_config.chunk_hours)
            ):
                summary = self.engine.sync(
                    chunk_start,
                    chunk_end,
                    entity_keys={entity_key},
                )
                rows_written += summary.rows_written
            self.target.complete_candidate_backfill(candidate_id, rows_written)
            LOGGER.info("EDC 历史补录完成 candidate_id=%s rows=%s", candidate_id, rows_written)
        except Exception as exc:
            self.target.fail_candidate_backfill(candidate_id, str(exc))
            LOGGER.exception("EDC 历史补录失败 candidate_id=%s", candidate_id)
            self.notifier.send(
                "EDC 历史补录失败",
                f"候选记录 ID：{candidate_id}\n错误：{exc}\n请检查源库、目标库和执行记录。",
            )


def _notification_level(candidate: dict, now: datetime) -> int:
    discovered_at = candidate.get("discovered_at") or candidate.get("first_seen_at")
    if not isinstance(discovered_at, datetime):
        return 1
    age = now - discovered_at
    if age >= timedelta(hours=24):
        return 3
    if age >= timedelta(hours=4):
        return 2
    return 1


def _notification_title(level: int) -> str:
    return {
        1: "发现新的未录入 EDC",
        2: "EDC 未录入已超过 4 小时",
        3: "EDC 未录入已超过 24 小时，可能影响结算",
    }.get(level, "EDC 录入提醒")


def _ceil_5m(value: datetime) -> datetime:
    value = value.replace(second=0, microsecond=0)
    remainder = value.minute % 5
    if remainder:
        value += timedelta(minutes=5 - remainder)
    return value
