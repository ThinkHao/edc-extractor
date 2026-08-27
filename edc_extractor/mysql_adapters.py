from __future__ import annotations

from datetime import datetime
from time import sleep
from typing import Callable, Iterable

import mysql.connector

from .config import DBConfig
from .entity_onboarding import ConfiguredEntityMapping, SourceEntityCandidate, is_backup_edc_name
from .sync_engine import EDCEntity


def connect(config: DBConfig):
    attempts = max(1, config.connect_retry_attempts)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return mysql.connector.connect(
                host=config.host,
                port=config.port,
                user=config.user,
                password=config.password,
                database=config.database,
                connection_timeout=config.connect_timeout_seconds,
                autocommit=False,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= attempts or not _is_retryable_connect_error(exc):
                raise
            sleep(config.connect_retry_backoff_seconds * attempt)
    raise last_error or RuntimeError("mysql connection failed")


def _is_retryable_connect_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "can't connect to mysql server" in message or "timed out" in message or "timeout" in message


def ping(config: DBConfig) -> None:
    conn = connect(config)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
    finally:
        conn.close()


class MySQLEDCSource:
    def __init__(self, config: DBConfig):
        self.config = config

    def ping(self) -> None:
        ping(self.config)

    def open_session(self):
        return MySQLEDCSourceSession(self.config)

    def fetch_aggregated_rows(self, start_time: datetime, end_time: datetime, edc_names: list[str]) -> Iterable[dict]:
        conn = connect(self.config)
        try:
            yield from _fetch_aggregated_rows(conn, self.config, start_time, end_time, edc_names)
        finally:
            conn.close()

    def has_recommended_index(self) -> bool:
        query = """
        SELECT COUNT(*)
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME IN (
            SELECT INDEX_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND COLUMN_NAME = 'edc_name'
              AND SEQ_IN_INDEX = 1
          )
          AND COLUMN_NAME = 'create_time'
          AND SEQ_IN_INDEX = 2
        """
        conn = connect(self.config)
        try:
            cursor = conn.cursor()
            cursor.execute(query, (self.config.table, self.config.table))
            row = cursor.fetchone()
            return bool(row and row[0] > 0)
        finally:
            conn.close()

    def discover_entity_candidates(
        self,
        start_time: datetime,
        end_time: datetime,
        limit: int = 500,
    ) -> list[SourceEntityCandidate]:
        query = f"""
        SELECT
          edc_name,
          COALESCE(sn, '') AS sn,
          MIN(create_time) AS first_seen_create_time,
          MAX(create_time) AS latest_create_time,
          COUNT(*) AS record_count
        FROM {self.config.table}
        WHERE create_time >= %s
          AND create_time < %s
        GROUP BY edc_name, COALESCE(sn, '')
        ORDER BY latest_create_time DESC, edc_name ASC, sn ASC
        LIMIT %s
        """
        conn = connect(self.config)
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(query, (start_time, end_time, limit))
            return [
                SourceEntityCandidate(
                    edc_name=str(row["edc_name"]),
                    sn=str(row.get("sn") or ""),
                    latest_create_time=row["latest_create_time"],
                    record_count=int(row["record_count"]),
                    first_seen_create_time=row.get("first_seen_create_time"),
                )
                for row in cursor
            ]
        finally:
            conn.close()

    def get_entity_time_bounds(self, edc_name: str, sn: str) -> tuple[datetime | None, datetime | None]:
        if sn:
            query = f"""
            SELECT MIN(create_time) AS first_seen_create_time,
                   MAX(create_time) AS latest_create_time
            FROM {self.config.table}
            WHERE sn = %s AND edc_name = %s
            """
            params = (sn, edc_name)
        else:
            query = f"""
            SELECT MIN(create_time) AS first_seen_create_time,
                   MAX(create_time) AS latest_create_time
            FROM {self.config.table}
            WHERE edc_name = %s AND (sn IS NULL OR sn = '')
            """
            params = (edc_name,)
        conn = connect(self.config)
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(query, params)
            row = cursor.fetchone() or {}
            return row.get("first_seen_create_time"), row.get("latest_create_time")
        finally:
            conn.close()


class MySQLEDCSourceSession:
    def __init__(self, config: DBConfig):
        self.config = config
        self.conn = None

    def __enter__(self):
        self.conn = connect(self.config)
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.conn is not None:
            self.conn.close()
        self.conn = None

    def fetch_aggregated_rows(self, start_time: datetime, end_time: datetime, edc_names: list[str]) -> Iterable[dict]:
        if self.conn is None:
            raise RuntimeError("source session is not open")
        self.conn.ping(
            reconnect=True,
            attempts=max(1, self.config.connect_retry_attempts),
            delay=int(self.config.connect_retry_backoff_seconds),
        )
        yield from _fetch_aggregated_rows(self.conn, self.config, start_time, end_time, edc_names)


def _fetch_aggregated_rows(conn, config: DBConfig, start_time: datetime, end_time: datetime, edc_names: list[str]) -> Iterable[dict]:
    if not edc_names:
        return []
    placeholders = ",".join(["%s"] * len(edc_names))
    query = f"""
    SELECT
      create_time,
      edc_name,
      COALESCE(sn, '') AS sn,
      SUM(service_size) AS service_size,
      SUM(cache_size) AS cache_size,
      COUNT(*) AS record_count
    FROM {config.table}
    WHERE create_time >= %s
      AND create_time < %s
      AND edc_name IN ({placeholders})
    GROUP BY create_time, edc_name, COALESCE(sn, '')
    ORDER BY create_time ASC, edc_name ASC, sn ASC
    """
    cursor = conn.cursor(dictionary=True)
    cursor.execute(query, [start_time, end_time, *edc_names])
    for row in cursor:
        yield row


class MySQLEDCTarget:
    def __init__(self, config: DBConfig):
        self.config = config

    def ping(self) -> None:
        ping(self.config)

    def open_session(self):
        return MySQLEDCTargetSession(self.config)

    def load_enabled_entities(self) -> list[EDCEntity]:
        conn = connect(self.config)
        try:
            _ensure_entity_schema(conn)
            return _load_enabled_entities(conn)
        finally:
            conn.close()

    def load_entity_keys(self) -> set[tuple[str, str]]:
        return set(self.load_entity_mappings())

    def list_entity_records(self) -> list[dict]:
        """返回全部已配置实体，包含已禁用实体，供状态管理页面使用。"""
        conn = connect(self.config)
        try:
            _ensure_entity_schema(conn)
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, edc_name, sn, display_name, alias, region, cp, entity_type,
                       src_region, dst_region, is_backup, enabled, remark, created_at, updated_at
                FROM edc_entities
                ORDER BY enabled DESC, is_backup ASC, edc_name ASC, sn ASC, id ASC
                """
            )
            return [dict(row) for row in cursor]
        finally:
            conn.close()

    def set_entity_enabled(
        self,
        entity_id: int,
        enabled: bool,
        *,
        operator: str = "web",
        source_ip: str | None = None,
    ) -> dict | None:
        """幂等切换实体状态，并在同一事务中记录审计。"""
        conn = connect(self.config)
        try:
            _ensure_entity_schema(conn)
            _ensure_entity_status_audit_schema(conn)
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, edc_name, sn, display_name, alias, region, cp, entity_type,
                       src_region, dst_region, is_backup, enabled, remark, created_at, updated_at
                FROM edc_entities
                WHERE id = %s
                FOR UPDATE
                """,
                (int(entity_id),),
            )
            before = cursor.fetchone()
            if not before:
                conn.rollback()
                return None
            before_enabled = bool(before.get("enabled"))
            after_enabled = bool(enabled)
            if before_enabled != after_enabled:
                cursor.execute(
                    "UPDATE edc_entities SET enabled = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (1 if after_enabled else 0, int(entity_id)),
                )
                cursor.execute(
                    """
                    INSERT INTO edc_entity_status_audit
                      (entity_id, edc_name, sn, enabled_before, enabled_after, operator, source_ip)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        int(entity_id),
                        str(before["edc_name"]),
                        str(before.get("sn") or ""),
                        1 if before_enabled else 0,
                        1 if after_enabled else 0,
                        _audit_text(operator, "web"),
                        _audit_text(source_ip, None),
                    ),
                )
            conn.commit()
            cursor.execute(
                """
                SELECT id, edc_name, sn, display_name, alias, region, cp, entity_type,
                       src_region, dst_region, is_backup, enabled, remark, created_at, updated_at
                FROM edc_entities
                WHERE id = %s
                """,
                (int(entity_id),),
            )
            result = cursor.fetchone()
            return dict(result) if result else None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def load_entity_mappings(self) -> dict[tuple[str, str], ConfiguredEntityMapping]:
        conn = connect(self.config)
        try:
            _ensure_entity_schema(conn)
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, edc_name, sn, display_name, alias, region, cp, entity_type, src_region, dst_region,
                       is_backup, enabled, remark
                FROM edc_entities
                """
            )
            return {
                (str(row["edc_name"]), str(row.get("sn") or "")): ConfiguredEntityMapping(
                    edc_name=str(row["edc_name"]),
                    sn=str(row.get("sn") or ""),
                    display_name=str(row["display_name"]),
                    alias=_optional_text(row.get("alias")),
                    region=str(row["region"]),
                    cp=str(row["cp"]),
                    entity_type=_optional_text(row.get("entity_type")),
                    src_region=_optional_text(row.get("src_region")),
                    dst_region=_optional_text(row.get("dst_region")),
                    is_backup=bool(row.get("is_backup")),
                    enabled=bool(row.get("enabled")),
                    remark=str(row.get("remark") or ""),
                    entity_id=int(row["id"]) if row.get("id") is not None else None,
                )
                for row in cursor
            }
        finally:
            conn.close()

    def upsert_entities(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        payload = [
            (
                str(row["edc_name"]).strip(),
                str(row.get("sn") or "").strip(),
                str(row["display_name"]).strip(),
                _optional_text(row.get("alias")),
                str(row["region"]).strip(),
                str(row["cp"]).strip(),
                _normalize_entity_type(row.get("entity_type")),
                _optional_text(row.get("src_region")),
                _optional_text(row.get("dst_region")),
                1 if row.get("is_backup", is_backup_edc_name(str(row["edc_name"]))) else 0,
                1 if row.get("enabled", True) else 0,
                str(row.get("remark") or "").strip(),
            )
            for row in rows
        ]
        conn = connect(self.config)
        try:
            _ensure_entity_schema(conn)
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO edc_entities
                  (edc_name, sn, display_name, alias, region, cp, entity_type, src_region, dst_region,
                   is_backup, enabled, remark)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  display_name = VALUES(display_name),
                  alias = VALUES(alias),
                  region = VALUES(region),
                  cp = VALUES(cp),
                  entity_type = VALUES(entity_type),
                  src_region = VALUES(src_region),
                  dst_region = VALUES(dst_region),
                  is_backup = VALUES(is_backup),
                  enabled = VALUES(enabled),
                  remark = VALUES(remark)
                """,
                payload,
            )
            conn.commit()
            return len(payload)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def entity_ids_for_keys(self, entity_keys: set[tuple[str, str]]) -> list[int]:
        if not entity_keys:
            return []
        conn = connect(self.config)
        try:
            cursor = conn.cursor()
            conditions = []
            params: list[str] = []
            for edc_name, sn in sorted(entity_keys):
                conditions.append("(edc_name = %s AND sn = %s)")
                params.extend((edc_name, sn or ""))
            cursor.execute(
                "SELECT id FROM edc_entities WHERE " + " OR ".join(conditions) + " ORDER BY id",
                params,
            )
            return [int(row[0]) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_entity_traffic_bounds(self, entity_ids: list[int]) -> tuple[datetime | None, datetime | None]:
        if not entity_ids:
            return None, None
        conn = connect(self.config)
        try:
            cursor = conn.cursor()
            placeholders = ",".join(["%s"] * len(entity_ids))
            cursor.execute(
                f"SELECT MIN(bucket_5m), MAX(bucket_5m) FROM edc_traffic_5m WHERE entity_id IN ({placeholders})",
                entity_ids,
            )
            row = cursor.fetchone() or (None, None)
            return row[0], row[1]
        finally:
            conn.close()

    def reconcile_traffic_metadata(
        self,
        entity_ids: list[int],
        progress_callback: Callable[[dict], None] | None = None,
    ) -> dict:
        """把历史流量行的映射快照幂等同步为当前实体映射，不改流量数值。"""
        unique_ids = sorted({int(entity_id) for entity_id in entity_ids})
        if not unique_ids:
            return {
                "kind": "metadata_reconcile",
                "total_entities": 0,
                "completed_entities": 0,
                "rows_scanned": 0,
                "rows_updated": 0,
                "percent": 100,
            }
        conn = connect(self.config)
        try:
            _ensure_entity_schema(conn)
            cursor = conn.cursor()
            rows_scanned = 0
            rows_updated = 0
            for completed, entity_id in enumerate(unique_ids, 1):
                cursor.execute(
                    "SELECT COUNT(*) FROM edc_traffic_5m WHERE entity_id = %s",
                    (entity_id,),
                )
                row = cursor.fetchone() or (0,)
                entity_rows = int(row[0] or 0)
                rows_scanned += entity_rows
                cursor.execute(
                    """
                    UPDATE edc_traffic_5m AS t
                    INNER JOIN edc_entities AS e ON e.id = t.entity_id
                    SET t.region = e.region,
                        t.cp = e.cp,
                        t.entity_type = e.entity_type,
                        t.src_region = e.src_region,
                        t.dst_region = e.dst_region,
                        t.alias = e.alias,
                        t.display_name = e.display_name,
                        t.updated_at = CURRENT_TIMESTAMP
                    WHERE t.entity_id = %s
                      AND (
                        NOT (t.region <=> e.region)
                        OR NOT (t.cp <=> e.cp)
                        OR NOT (t.entity_type <=> e.entity_type)
                        OR NOT (t.src_region <=> e.src_region)
                        OR NOT (t.dst_region <=> e.dst_region)
                        OR NOT (t.alias <=> e.alias)
                        OR NOT (t.display_name <=> e.display_name)
                      )
                    """,
                    (entity_id,),
                )
                changed = int(cursor.rowcount or 0)
                rows_updated += changed
                conn.commit()
                if progress_callback:
                    progress_callback(
                        {
                            "kind": "metadata_reconcile",
                            "total_entities": len(unique_ids),
                            "completed_entities": completed,
                            "percent": int(completed * 100 / len(unique_ids)),
                            "entity_id": entity_id,
                            "rows_scanned": rows_scanned,
                            "rows_updated": rows_updated,
                        }
                    )
            return {
                "kind": "metadata_reconcile",
                "total_entities": len(unique_ids),
                "completed_entities": len(unique_ids),
                "rows_scanned": rows_scanned,
                "rows_updated": rows_updated,
                "percent": 100,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def upsert_entity_candidates(
        self,
        candidates: list[SourceEntityCandidate],
        configured_keys: set[tuple[str, str]] | None = None,
    ) -> int:
        if not candidates:
            return 0
        _ensure_candidate_schema_for_config(self.config)
        configured_keys = configured_keys or set()
        payload = [
            (
                candidate.edc_name.strip(),
                candidate.sn.strip(),
                1 if is_backup_edc_name(candidate.edc_name) else 0,
                candidate.first_seen_create_time or candidate.latest_create_time,
                candidate.latest_create_time,
                int(candidate.record_count),
                "registered" if (candidate.edc_name, candidate.sn) in configured_keys else "pending",
            )
            for candidate in candidates
        ]
        conn = connect(self.config)
        try:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO edc_entity_candidates
                  (edc_name, sn, is_backup, first_seen_at, latest_seen_at, record_count, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  is_backup = VALUES(is_backup),
                  first_seen_at = LEAST(first_seen_at, VALUES(first_seen_at)),
                  latest_seen_at = GREATEST(latest_seen_at, VALUES(latest_seen_at)),
                  record_count = GREATEST(record_count, VALUES(record_count)),
                  updated_at = CURRENT_TIMESTAMP
                """,
                payload,
            )
            conn.commit()
            return len(payload)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_entity_candidates(
        self,
        statuses: set[str] | None = None,
        limit: int = 500,
        entity_keys: set[tuple[str, str]] | None = None,
    ) -> list[dict]:
        _ensure_candidate_schema_for_config(self.config)
        conn = connect(self.config)
        try:
            cursor = conn.cursor(dictionary=True)
            params: list[object] = []
            where = ""
            if statuses:
                values = sorted(statuses)
                where = " WHERE status IN (" + ",".join(["%s"] * len(values)) + ")"
                params.extend(values)
                if statuses.issubset({"pending", "failed"}):
                    where += " AND enabled = 1"
            if entity_keys:
                key_conditions = []
                for edc_name, sn in sorted(entity_keys):
                    key_conditions.append("(edc_name = %s AND sn = %s)")
                    params.extend((edc_name, sn or ""))
                where += (" AND " if where else " WHERE ") + "(" + " OR ".join(key_conditions) + ")"
            cursor.execute(
                "SELECT * FROM edc_entity_candidates" + where + " ORDER BY updated_at DESC, id DESC LIMIT %s",
                [*params, max(1, min(int(limit), 5000))],
            )
            return [dict(row) for row in cursor]
        finally:
            conn.close()

    def list_entity_candidate_states(
        self,
        entity_keys: set[tuple[str, str]],
    ) -> dict[tuple[str, str], dict]:
        """返回发现页候选条目的持久化状态，供待录入行显示和操作。"""
        rows = self.list_entity_candidates(
            limit=max(1, len(entity_keys)),
            entity_keys=entity_keys,
        )
        return {
            (str(row["edc_name"]), str(row.get("sn") or "")): dict(row)
            for row in rows
        }

    def set_entity_candidate_enabled(
        self,
        candidate_id: int,
        enabled: bool,
        *,
        operator: str = "web",
        source_ip: str | None = None,
    ) -> dict | None:
        """幂等切换待录入候选条目状态，并记录审计。"""
        conn = connect(self.config)
        try:
            _ensure_candidate_schema_for_config(self.config)
            _ensure_candidate_status_audit_schema(conn)
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, edc_name, sn, enabled, status, entity_id
                FROM edc_entity_candidates
                WHERE id = %s
                FOR UPDATE
                """,
                (int(candidate_id),),
            )
            before = cursor.fetchone()
            if not before:
                conn.rollback()
                return None
            if before.get("entity_id") is not None or str(before.get("status") or "") not in {
                "pending",
                "failed",
                "disabled",
            }:
                raise ValueError("该候选条目已进入映射或补录流程，不能在待录入状态下切换")

            before_enabled = bool(before.get("enabled", 1))
            after_enabled = bool(enabled)
            if before_enabled != after_enabled:
                next_status = "pending" if after_enabled and before.get("status") == "disabled" else (
                    "disabled" if not after_enabled else str(before.get("status") or "pending")
                )
                cursor.execute(
                    "UPDATE edc_entity_candidates SET enabled = %s, status = %s, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (1 if after_enabled else 0, next_status, int(candidate_id)),
                )
                cursor.execute(
                    """
                    INSERT INTO edc_entity_candidate_status_audit
                      (candidate_id, edc_name, sn, enabled_before, enabled_after, operator, source_ip)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        int(candidate_id),
                        str(before["edc_name"]),
                        str(before.get("sn") or ""),
                        1 if before_enabled else 0,
                        1 if after_enabled else 0,
                        _audit_text(operator, "web"),
                        _audit_text(source_ip, None),
                    ),
                )
            conn.commit()
            cursor.execute(
                """
                SELECT id, edc_name, sn, enabled, status, entity_id, updated_at
                FROM edc_entity_candidates
                WHERE id = %s
                """,
                (int(candidate_id),),
            )
            result = cursor.fetchone()
            return dict(result) if result else None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mark_candidate_backfill_pending(
        self,
        candidate_id: int,
        entity_id: int,
        start_time: datetime,
        end_time: datetime,
    ) -> bool:
        _ensure_candidate_schema_for_config(self.config)
        conn = connect(self.config)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE edc_entity_candidates
                SET status = 'backfill_pending', entity_id = %s, backfill_start_at = %s,
                    backfill_end_at = %s, backfill_error = NULL, confirmed_at = COALESCE(confirmed_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status IN ('pending', 'failed') AND enabled = 1
                """,
                (entity_id, start_time, end_time, candidate_id),
            )
            conn.commit()
            return cursor.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def claim_candidate_backfill(self, candidate_id: int) -> bool:
        _ensure_candidate_schema_for_config(self.config)
        conn = connect(self.config)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE edc_entity_candidates SET status = 'backfilling', updated_at = CURRENT_TIMESTAMP "
                "WHERE id = %s AND status = 'backfill_pending'",
                (candidate_id,),
            )
            conn.commit()
            return cursor.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def complete_candidate_backfill(self, candidate_id: int, rows_written: int) -> None:
        self._update_candidate_backfill(candidate_id, "ready", rows_written, None)

    def fail_candidate_backfill(self, candidate_id: int, error: str) -> None:
        self._update_candidate_backfill(candidate_id, "failed", 0, error[:2000])

    def reset_candidate_for_retry(self, candidate_id: int) -> bool:
        _ensure_candidate_schema_for_config(self.config)
        conn = connect(self.config)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE edc_entity_candidates SET status = 'pending', backfill_error = NULL, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = %s AND status = 'failed' AND enabled = 1",
                (candidate_id,),
            )
            conn.commit()
            return cursor.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record_candidate_notification(self, candidate_id: int, level: int, sent_at: datetime) -> None:
        _ensure_candidate_schema_for_config(self.config)
        conn = connect(self.config)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE edc_entity_candidates SET last_notified_level = %s, last_notified_at = %s, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (level, sent_at, candidate_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _update_candidate_backfill(self, candidate_id: int, status: str, rows_written: int, error: str | None) -> None:
        _ensure_candidate_schema_for_config(self.config)
        conn = connect(self.config)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE edc_entity_candidates SET status = %s, backfill_rows = %s, backfill_error = %s, "
                "backfill_completed_at = CASE WHEN %s = 'ready' THEN CURRENT_TIMESTAMP ELSE NULL END, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (status, rows_written, error, status, candidate_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def upsert_traffic_rows(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        conn = connect(self.config)
        try:
            _ensure_entity_schema(conn)
            entity_map = {entity.id: entity for entity in _load_enabled_entities(conn)}
            return _upsert_traffic_rows(conn, rows, entity_map)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class MySQLEDCTargetSession:
    def __init__(self, config: DBConfig):
        self.config = config
        self.conn = None
        self.entity_map: dict[int, EDCEntity] = {}

    def __enter__(self):
        self.conn = connect(self.config)
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.conn is not None:
            self.conn.close()
        self.conn = None
        self.entity_map = {}

    def load_enabled_entities(self) -> list[EDCEntity]:
        if self.conn is None:
            raise RuntimeError("target session is not open")
        _ensure_entity_schema(self.conn)
        entities = _load_enabled_entities(self.conn)
        self.entity_map = {entity.id: entity for entity in entities}
        return entities

    def upsert_traffic_rows(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        if self.conn is None:
            raise RuntimeError("target session is not open")
        if not self.entity_map:
            self.load_enabled_entities()
        try:
            return _upsert_traffic_rows(self.conn, rows, self.entity_map)
        except Exception:
            self.conn.rollback()
            raise


def _load_enabled_entities(conn) -> list[EDCEntity]:
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT id, edc_name, sn, display_name, alias, region, cp, entity_type, src_region, dst_region, is_backup
        FROM edc_entities
        WHERE enabled = 1
        ORDER BY edc_name ASC, sn ASC
        """
    )
    return [
        EDCEntity(
            id=int(row["id"]),
            edc_name=str(row["edc_name"]),
            sn=str(row.get("sn") or ""),
            display_name=str(row["display_name"]),
            alias=_optional_text(row.get("alias")),
            region=str(row["region"]),
            cp=str(row["cp"]),
            entity_type=_optional_text(row.get("entity_type")),
            src_region=_optional_text(row.get("src_region")),
            dst_region=_optional_text(row.get("dst_region")),
            is_backup=bool(row.get("is_backup")),
        )
        for row in cursor
    ]


def _ensure_entity_schema(conn) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS edc_entities (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          edc_name VARCHAR(255) NOT NULL,
          sn VARCHAR(255) NOT NULL DEFAULT '',
          display_name VARCHAR(255) NOT NULL,
          alias VARCHAR(255) NULL,
          region VARCHAR(255) NOT NULL,
          cp VARCHAR(255) NOT NULL,
          entity_type VARCHAR(20) NULL,
          src_region VARCHAR(20) NULL,
          dst_region VARCHAR(20) NULL,
          is_backup TINYINT(1) NOT NULL DEFAULT 0,
          enabled TINYINT(1) NOT NULL DEFAULT 1,
          remark TEXT NULL,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_edc_entities_name_sn (edc_name, sn)
        )
        """
    )
    conn.commit()
    _ensure_optional_entity_columns(conn)
    _ensure_optional_traffic_columns(conn)
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'edc_entities'
          AND COLUMN_NAME = 'is_backup'
        """
    )
    row = cursor.fetchone()
    if not row or int(row[0]) == 0:
        cursor.execute("ALTER TABLE edc_entities ADD COLUMN is_backup TINYINT(1) NOT NULL DEFAULT 0 AFTER cp")
        cursor.execute("UPDATE edc_entities SET is_backup = CASE WHEN LOWER(edc_name) LIKE '%backup%' THEN 1 ELSE 0 END")
        conn.commit()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'edc_entities'
          AND INDEX_NAME = 'idx_edc_entities_backup_enabled'
        """
    )
    row = cursor.fetchone()
    if not row or int(row[0]) == 0:
        cursor.execute("CREATE INDEX idx_edc_entities_backup_enabled ON edc_entities (is_backup, enabled)")
        conn.commit()


def _ensure_entity_status_audit_schema(conn) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS edc_entity_status_audit (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          entity_id BIGINT NOT NULL,
          edc_name VARCHAR(255) NOT NULL,
          sn VARCHAR(255) NOT NULL DEFAULT '',
          enabled_before TINYINT(1) NOT NULL,
          enabled_after TINYINT(1) NOT NULL,
          operator VARCHAR(128) NOT NULL DEFAULT 'web',
          source_ip VARCHAR(64) NULL,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_edc_entity_status_audit_entity_time (entity_id, created_at)
        )
        """
    )
    conn.commit()


def _audit_text(value, default: str | None) -> str | None:
    if value is None:
        return default
    normalized = str(value).strip()
    return normalized or default


def _upsert_traffic_rows(conn, rows: list[dict], entity_map: dict[int, EDCEntity]) -> int:
    payload = []
    for row in rows:
        entity = entity_map.get(int(row["entity_id"]))
        if not entity:
            continue
        payload.append(
            (
                row["bucket_5m"],
                entity.id,
                entity.region,
                entity.cp,
                entity.entity_type,
                entity.src_region,
                entity.dst_region,
                entity.alias,
                entity.display_name,
                int(row["service_size"]),
                int(row["cache_size"]),
                int(row["record_count"]),
            )
        )
    if not payload:
        return 0
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT INTO edc_traffic_5m
          (bucket_5m, entity_id, region, cp, entity_type, src_region, dst_region, alias, display_name,
           service_size, cache_size, record_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          region = VALUES(region),
          cp = VALUES(cp),
          entity_type = VALUES(entity_type),
          src_region = VALUES(src_region),
          dst_region = VALUES(dst_region),
          alias = VALUES(alias),
          display_name = VALUES(display_name),
          service_size = VALUES(service_size),
          cache_size = VALUES(cache_size),
          record_count = VALUES(record_count),
          updated_at = CURRENT_TIMESTAMP
        """,
        payload,
    )
    conn.commit()
    return len(payload)


def _optional_text(value) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_entity_type(value) -> str | None:
    normalized = _optional_text(value)
    if normalized is None:
        return None
    if normalized not in {"node", "transmission"}:
        raise ValueError("entity_type must be node or transmission")
    return normalized


def _ensure_optional_entity_columns(conn) -> None:
    cursor = conn.cursor()
    definitions = {
        "entity_type": "VARCHAR(20) NULL AFTER cp",
        "alias": "VARCHAR(255) NULL AFTER display_name",
        "src_region": "VARCHAR(20) NULL AFTER entity_type",
        "dst_region": "VARCHAR(20) NULL AFTER src_region",
    }
    for column, definition in definitions.items():
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'edc_entities'
              AND COLUMN_NAME = %s
            """,
            (column,),
        )
        row = cursor.fetchone()
        if not row or int(row[0]) == 0:
            cursor.execute(f"ALTER TABLE edc_entities ADD COLUMN {column} {definition}")
            conn.commit()


def _ensure_optional_traffic_columns(conn) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'edc_traffic_5m'
        """
    )
    row = cursor.fetchone()
    if not row or int(row[0]) == 0:
        return
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'edc_traffic_5m'
          AND COLUMN_NAME = 'alias'
        """
    )
    row = cursor.fetchone()
    if not row or int(row[0]) == 0:
        cursor.execute("ALTER TABLE edc_traffic_5m ADD COLUMN alias VARCHAR(255) NULL AFTER dst_region")
        conn.commit()


def _ensure_candidate_schema_for_config(config: DBConfig) -> None:
    conn = connect(config)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS edc_entity_candidates (
              id BIGINT AUTO_INCREMENT PRIMARY KEY,
              edc_name VARCHAR(255) NOT NULL,
              sn VARCHAR(255) NOT NULL DEFAULT '',
              is_backup TINYINT(1) NOT NULL DEFAULT 0,
              first_seen_at DATETIME NULL,
              latest_seen_at DATETIME NULL,
              record_count BIGINT NOT NULL DEFAULT 0,
              enabled TINYINT(1) NOT NULL DEFAULT 1,
              status VARCHAR(32) NOT NULL DEFAULT 'pending',
              last_notified_level TINYINT NOT NULL DEFAULT 0,
              last_notified_at DATETIME NULL,
              entity_id BIGINT NULL,
              backfill_start_at DATETIME NULL,
              backfill_end_at DATETIME NULL,
              backfill_rows BIGINT NOT NULL DEFAULT 0,
              backfill_error TEXT NULL,
              discovered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              confirmed_at DATETIME NULL,
              backfill_completed_at DATETIME NULL,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              UNIQUE KEY uq_edc_entity_candidates_name_sn (edc_name, sn),
              KEY idx_edc_entity_candidates_status (status, updated_at)
            )
            """
        )
        conn.commit()
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'edc_entity_candidates'
              AND COLUMN_NAME = 'is_backup'
            """
        )
        row = cursor.fetchone()
        if not row or int(row[0]) == 0:
            cursor.execute("ALTER TABLE edc_entity_candidates ADD COLUMN is_backup TINYINT(1) NOT NULL DEFAULT 0 AFTER sn")
            cursor.execute(
                "UPDATE edc_entity_candidates SET is_backup = CASE WHEN LOWER(edc_name) LIKE '%backup%' THEN 1 ELSE 0 END"
            )
            conn.commit()
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'edc_entity_candidates'
              AND COLUMN_NAME = 'enabled'
            """
        )
        row = cursor.fetchone()
        if not row or int(row[0]) == 0:
            cursor.execute(
                "ALTER TABLE edc_entity_candidates ADD COLUMN enabled TINYINT(1) NOT NULL DEFAULT 1 AFTER record_count"
            )
            conn.commit()
    finally:
        conn.close()


def _ensure_candidate_status_audit_schema(conn) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS edc_entity_candidate_status_audit (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          candidate_id BIGINT NOT NULL,
          edc_name VARCHAR(255) NOT NULL,
          sn VARCHAR(255) NOT NULL DEFAULT '',
          enabled_before TINYINT(1) NOT NULL,
          enabled_after TINYINT(1) NOT NULL,
          operator VARCHAR(128) NOT NULL DEFAULT 'web',
          source_ip VARCHAR(64) NULL,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_edc_entity_candidate_status_audit_candidate_time (candidate_id, created_at)
        )
        """
    )
    conn.commit()
