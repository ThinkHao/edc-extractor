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
    notification: "NotificationConfig"
    app_host: str
    app_port: int
    debug: bool


@dataclass(frozen=True)
class SchedulerConfig:
    default_cron: str
    time_window_minutes: int
    delay_minutes: int
    enabled: bool = True
    discovery_cron: str = "*/5 * * * *"


@dataclass(frozen=True)
class NotificationConfig:
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    chat_id: str = ""
    mention_open_id: str = ""
    mention_name: str = "郝金鑫"
    webhook_url: str = ""
    start_hour: int = 9
    end_hour: int = 18


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
        discovery_cron=parser.get("scheduler", "discovery_cron", fallback="*/5 * * * *"),
    )
    notification = NotificationConfig(
        enabled=_env_bool_or_config(parser, "notification", "enabled", "EDC_FEISHU_ENABLED", False),
        app_id=_env_or_config(parser, "notification", "app_id", "EDC_FEISHU_APP_ID"),
        app_secret=_env_or_config(parser, "notification", "app_secret", "EDC_FEISHU_APP_SECRET"),
        chat_id=_env_or_config(parser, "notification", "chat_id", "EDC_FEISHU_CHAT_ID"),
        mention_open_id=_env_or_config(parser, "notification", "mention_open_id", "EDC_FEISHU_MENTION_OPEN_ID"),
        mention_name=_env_or_config(parser, "notification", "mention_name", "EDC_FEISHU_MENTION_NAME") or "郝金鑫",
        webhook_url=_env_or_config(parser, "notification", "webhook_url", "EDC_FEISHU_WEBHOOK_URL"),
        start_hour=_env_int_or_config(parser, "notification", "start_hour", "EDC_FEISHU_START_HOUR", 9),
        end_hour=_env_int_or_config(parser, "notification", "end_hour", "EDC_FEISHU_END_HOUR", 18),
    )
    return AppConfig(
        source=source,
        target=target,
        sync=sync,
        scheduler=scheduler,
        notification=notification,
        app_host=parser.get("app", "host", fallback="0.0.0.0"),
        app_port=parser.getint("app", "port", fallback=8081),
        debug=parser.getboolean("app", "debug", fallback=False),
    )


def _env_or_config(parser: ConfigParser, section: str, option: str, env_name: str) -> str:
    import os

    value = os.environ.get(env_name)
    if value is not None:
        return value.strip()
    return parser.get(section, option, fallback="").strip()


def _env_bool_or_config(parser: ConfigParser, section: str, option: str, env_name: str, fallback: bool) -> bool:
    import os

    value = os.environ.get(env_name)
    if value is not None:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return parser.getboolean(section, option, fallback=fallback)


def _env_int_or_config(parser: ConfigParser, section: str, option: str, env_name: str, fallback: int) -> int:
    import os

    value = os.environ.get(env_name)
    if value is not None:
        try:
            return int(value)
        except ValueError:
            pass
    return parser.getint(section, option, fallback=fallback)


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
