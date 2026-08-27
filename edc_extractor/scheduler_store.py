from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .sync_engine import SyncSummary

MIGRATIONS = [
    (
        "001",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version TEXT PRIMARY KEY,
          applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS edc_scheduled_tasks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          cron_expression TEXT NOT NULL,
          time_window_minutes INTEGER NOT NULL,
          delay_minutes INTEGER NOT NULL DEFAULT 10,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS edc_task_executions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id INTEGER,
          start_time TEXT NOT NULL,
          end_time TEXT,
          status TEXT NOT NULL,
          data_start_time TEXT NOT NULL,
          data_end_time TEXT NOT NULL,
          rows_read INTEGER NOT NULL DEFAULT 0,
          rows_written INTEGER NOT NULL DEFAULT 0,
          unmapped_count INTEGER NOT NULL DEFAULT 0,
          duration_ms INTEGER NOT NULL DEFAULT 0,
          error_message TEXT,
          progress_info TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_edc_task_executions_status ON edc_task_executions(status);
        CREATE INDEX IF NOT EXISTS idx_edc_task_executions_start ON edc_task_executions(start_time);
        """,
    )
]


class SchedulerStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.apply_migrations()

    def connect(self):
        return sqlite3.connect(self.path)

    def apply_migrations(self) -> list[str]:
        applied: list[str] = []
        with self.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            existing = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            for version, sql in MIGRATIONS:
                if version in existing:
                    continue
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, datetime.now().isoformat(timespec="seconds")),
                )
                applied.append(version)
        return applied

    def ensure_default_task(
        self,
        *,
        name: str,
        cron_expression: str,
        time_window_minutes: int,
        delay_minutes: int,
        enabled: bool,
    ) -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM edc_scheduled_tasks WHERE name = ?", (name,)).fetchone()
            if row:
                return dict(row)
            cur = conn.execute(
                """
                INSERT INTO edc_scheduled_tasks
                  (name, cron_expression, time_window_minutes, delay_minutes, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    cron_expression,
                    int(time_window_minutes),
                    int(delay_minutes),
                    1 if enabled else 0,
                    now,
                    now,
                ),
            )
            return dict(conn.execute("SELECT * FROM edc_scheduled_tasks WHERE id = ?", (cur.lastrowid,)).fetchone())

    def list_scheduled_tasks(self) -> list[dict]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute("SELECT * FROM edc_scheduled_tasks ORDER BY id ASC")]

    def get_scheduled_task(self, task_id: int) -> dict | None:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM edc_scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def update_scheduled_task(
        self,
        task_id: int,
        *,
        cron_expression: str,
        time_window_minutes: int,
        delay_minutes: int,
        enabled: bool,
    ) -> dict | None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                """
                UPDATE edc_scheduled_tasks
                SET cron_expression = ?,
                    time_window_minutes = ?,
                    delay_minutes = ?,
                    enabled = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    cron_expression,
                    int(time_window_minutes),
                    int(delay_minutes),
                    1 if enabled else 0,
                    now,
                    task_id,
                ),
            )
            row = conn.execute("SELECT * FROM edc_scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def has_running_execution(self) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM edc_task_executions WHERE status = 'running' LIMIT 1").fetchone()
            return row is not None

    def create_execution(
        self,
        task_id: int | None,
        data_start_time: datetime,
        data_end_time: datetime,
        *,
        kind: str = "traffic_sync",
    ) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        progress = {
            "kind": kind,
            "total_chunks": 0,
            "completed_chunks": 0,
            "percent": 0,
            "rows_read": 0,
            "rows_written": 0,
            "unmapped_count": 0,
        }
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO edc_task_executions
                  (task_id, start_time, status, data_start_time, data_end_time, progress_info)
                VALUES (?, ?, 'running', ?, ?, ?)
                """,
                (
                    task_id,
                    now,
                    data_start_time.isoformat(sep=" "),
                    data_end_time.isoformat(sep=" "),
                    json.dumps(progress, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid)

    def complete_custom_execution(self, execution_id: int, progress: dict) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        payload = dict(progress)
        payload.setdefault("percent", 100)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE edc_task_executions
                SET status = 'completed',
                    end_time = ?,
                    rows_read = ?,
                    rows_written = ?,
                    unmapped_count = 0,
                    duration_ms = ?,
                    progress_info = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    now,
                    int(payload.get("rows_scanned") or payload.get("rows_read") or 0),
                    int(payload.get("rows_updated") or payload.get("rows_written") or 0),
                    int(payload.get("duration_ms") or 0),
                    json.dumps(payload, default=str, ensure_ascii=False),
                    now,
                    execution_id,
                ),
            )

    def update_progress(self, execution_id: int, progress: dict) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE edc_task_executions
                SET rows_read = ?,
                    rows_written = ?,
                    unmapped_count = ?,
                    duration_ms = ?,
                    progress_info = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    int(progress.get("rows_read") or 0),
                    int(progress.get("rows_written") or 0),
                    int(progress.get("unmapped_count") or 0),
                    int(progress.get("duration_ms") or 0),
                    json.dumps(progress, ensure_ascii=False),
                    now,
                    execution_id,
                ),
            )

    def complete_execution(self, execution_id: int, summary: SyncSummary) -> None:
        total_chunks = len(summary.chunks)
        progress = {
            "total_chunks": total_chunks,
            "completed_chunks": total_chunks,
            "percent": 100,
            "rows_read": summary.rows_read,
            "rows_written": summary.rows_written,
            "unmapped_count": summary.unmapped_count,
            "negative_service_count": summary.negative_service_count,
            "negative_cache_count": summary.negative_cache_count,
            "duration_ms": summary.duration_ms,
            "chunks": [asdict(chunk) for chunk in summary.chunks],
        }
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE edc_task_executions
                SET status = 'completed',
                    end_time = ?,
                    rows_read = ?,
                    rows_written = ?,
                    unmapped_count = ?,
                    duration_ms = ?,
                    progress_info = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    summary.rows_read,
                    summary.rows_written,
                    summary.unmapped_count,
                    summary.duration_ms,
                    json.dumps(progress, default=str, ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                    execution_id,
                ),
            )

    def fail_execution(self, execution_id: int, error: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE edc_task_executions
                SET status = 'failed', end_time = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, error, now, execution_id),
            )

    def get_execution(self, execution_id: int) -> dict | None:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM edc_task_executions WHERE id = ?", (execution_id,)).fetchone()
            return dict(row) if row else None

    def list_executions(self, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(
                "SELECT * FROM edc_task_executions ORDER BY id DESC LIMIT ?",
                (limit,),
            )]
