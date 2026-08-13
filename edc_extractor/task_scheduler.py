from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import SchedulerConfig
from .scheduler_store import SchedulerStore
from .sync_engine import SyncEngine
from .sync_jobs import run_sync_job

DEFAULT_TASK_NAME = "默认自动同步"


class EDCTaskScheduler:
    def __init__(
        self,
        *,
        store: SchedulerStore,
        engine: SyncEngine,
        config: SchedulerConfig,
        source_host: str,
        target_host: str,
        executor: ThreadPoolExecutor,
        onboarding_cycle: Callable[[], object] | None = None,
    ):
        self.store = store
        self.engine = engine
        self.config = config
        self.source_host = source_host
        self.target_host = target_host
        self.executor = executor
        self.onboarding_cycle = onboarding_cycle
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    def start(self) -> None:
        self.ensure_default_task()
        self.reload_jobs()
        if not self.scheduler.running:
            self.scheduler.start()

    def ensure_default_task(self) -> dict:
        self.store.ensure_default_task(
            name=DEFAULT_TASK_NAME,
            cron_expression=self.config.default_cron,
            time_window_minutes=self.config.time_window_minutes,
            delay_minutes=self.config.delay_minutes,
            enabled=self.config.enabled,
        )
        return self.store.list_scheduled_tasks()[0]

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def reload_jobs(self) -> None:
        self.scheduler.remove_all_jobs()
        for task in self.store.list_scheduled_tasks():
            if not int(task["enabled"]):
                continue
            trigger = CronTrigger.from_crontab(task["cron_expression"], timezone="Asia/Shanghai")
            self.scheduler.add_job(
                self.submit_task,
                trigger=trigger,
                args=[int(task["id"])],
                id=self._job_id(task["id"]),
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
        if self.onboarding_cycle:
            trigger = CronTrigger.from_crontab(self.config.discovery_cron, timezone="Asia/Shanghai")
            self.scheduler.add_job(
                self.onboarding_cycle,
                trigger=trigger,
                id="edc-onboarding-monitor",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )

    def submit_task(self, task_id: int) -> int | None:
        task = self.store.get_scheduled_task(task_id)
        if not task or not int(task["enabled"]):
            return None
        if self.store.has_running_execution():
            return None
        start_time, end_time = scheduled_window(
            datetime.now(),
            time_window_minutes=int(task["time_window_minutes"]),
            delay_minutes=int(task["delay_minutes"]),
        )
        execution_id = self.store.create_execution(task_id, start_time, end_time)
        self.executor.submit(
            run_sync_job,
            self.store,
            self.engine,
            execution_id,
            start_time,
            end_time,
            self.source_host,
            self.target_host,
        )
        return execution_id

    def job_state(self, task_id: int) -> dict:
        job = self.scheduler.get_job(self._job_id(task_id))
        next_run_time = getattr(job, "next_run_time", None) if job else None
        return {"next_run_time": next_run_time.isoformat() if next_run_time else None}

    @staticmethod
    def _job_id(task_id: int | str) -> str:
        return f"edc-task-{task_id}"


def scheduled_window(now: datetime, *, time_window_minutes: int, delay_minutes: int) -> tuple[datetime, datetime]:
    delayed = now - timedelta(minutes=delay_minutes)
    end_time = delayed.replace(second=0, microsecond=0)
    end_time = end_time - timedelta(minutes=end_time.minute % 5)
    start_time = end_time - timedelta(minutes=time_window_minutes)
    return start_time, end_time


def validate_cron_expression(value: str) -> str:
    expression = value.strip()
    CronTrigger.from_crontab(expression, timezone="Asia/Shanghai")
    return expression
