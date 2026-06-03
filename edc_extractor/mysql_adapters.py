from __future__ import annotations

from datetime import datetime
from time import sleep
from typing import Iterable

import mysql.connector

from .config import DBConfig
from .entity_onboarding import SourceEntityCandidate, is_backup_edc_name
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
                )
                for row in cursor
            ]
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
        conn = connect(self.config)
        try:
            _ensure_entity_schema(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT edc_name, sn FROM edc_entities")
            return {(str(row[0]), str(row[1] or "")) for row in cursor}
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
                str(row["region"]).strip(),
                str(row["cp"]).strip(),
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
                  (edc_name, sn, display_name, region, cp, is_backup, enabled, remark)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  display_name = VALUES(display_name),
                  region = VALUES(region),
                  cp = VALUES(cp),
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
        SELECT id, edc_name, sn, display_name, region, cp, is_backup
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
            region=str(row["region"]),
            cp=str(row["cp"]),
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
          region VARCHAR(255) NOT NULL,
          cp VARCHAR(255) NOT NULL,
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
          (bucket_5m, entity_id, region, cp, display_name, service_size, cache_size, record_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          region = VALUES(region),
          cp = VALUES(cp),
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
