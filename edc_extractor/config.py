from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path

from .sync_engine import SyncConfig


@dataclass(frozen=True)
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    table: str = "edc_data"
    connect_timeout_seconds: int = 10
    read_timeout_seconds: int = 120
    connect_retry_attempts: int = 3
    connect_retry_backoff_seconds: float = 1.0


@dataclass(frozen=True)
class AppConfig:
    source: DBConfig
    target: DBConfig
    sync: SyncConfig
    scheduler: "SchedulerConfig"
    app_host: str
    app_port: int
    debug: bool


@dataclass(frozen=True)
class SchedulerConfig:
    default_cron: str
    time_window_minutes: int
    delay_minutes: int
    enabled: bool = True


def load_config(path: str | Path = "config.ini") -> AppConfig:
    parser = ConfigParser()
    if not parser.read(path, encoding="utf-8"):
        raise FileNotFoundError(f"config file not found: {path}")

    source = _db_config(parser, "db_source", default_table="edc_data")
    target = _db_config(parser, "db_target", default_table="")
    sync = SyncConfig(
        chunk_hours=parser.getint("sync", "chunk_hours", fallback=6),
        edc_name_batch_size=parser.getint("sync", "edc_name_batch_size", fallback=200),
        insert_batch_size=parser.getint("sync", "insert_batch_size", fallback=5000),
        max_manual_range_days=parser.getint("sync", "max_manual_range_days", fallback=31),
        allow_large_manual_range=parser.getboolean("sync", "allow_large_manual_range", fallback=False),
    )
    scheduler = SchedulerConfig(
        default_cron=parser.get("scheduler", "default_cron", fallback="*/10 * * * *"),
        time_window_minutes=parser.getint("scheduler", "time_window_minutes", fallback=60),
        delay_minutes=parser.getint("scheduler", "delay_minutes", fallback=10),
        enabled=parser.getboolean("scheduler", "enabled", fallback=True),
    )
    return AppConfig(
        source=source,
        target=target,
        sync=sync,
        scheduler=scheduler,
        app_host=parser.get("app", "host", fallback="0.0.0.0"),
        app_port=parser.getint("app", "port", fallback=8081),
        debug=parser.getboolean("app", "debug", fallback=False),
    )


def _db_config(parser: ConfigParser, section: str, default_table: str) -> DBConfig:
    return DBConfig(
        host=parser.get(section, "host"),
        port=parser.getint(section, "port", fallback=3306),
        user=parser.get(section, "user"),
        password=parser.get(section, "password", fallback=""),
        database=parser.get(section, "database"),
        table=parser.get(section, "table", fallback=default_table),
        connect_timeout_seconds=parser.getint(section, "connect_timeout_seconds", fallback=10),
        read_timeout_seconds=parser.getint(section, "read_timeout_seconds", fallback=120),
        connect_retry_attempts=parser.getint(section, "connect_retry_attempts", fallback=3),
        connect_retry_backoff_seconds=parser.getfloat(section, "connect_retry_backoff_seconds", fallback=1.0),
    )
