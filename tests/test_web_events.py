import json
from pathlib import Path

import edc_extractor.web as web_module


def write_config(path: Path) -> None:
    path.write_text(
        """
[db_source]
host = source.example
port = 3306
user = root
password =
database = cloud
table = edc_data

[db_target]
host = target.example
port = 3306
user = root
password =
database = nfa

[app]
host = 127.0.0.1
port = 8081
debug = false

[scheduler]
default_cron = */10 * * * *
time_window_minutes = 60
delay_minutes = 10
enabled = true

[sync]
chunk_hours = 6
edc_name_batch_size = 200
insert_batch_size = 5000
max_manual_range_days = 31
allow_large_manual_range = false
""".strip(),
        encoding="utf-8",
    )


class HealthySource:
    def __init__(self, config):
        self.config = config

    def ping(self):
        return None

    def has_recommended_index(self):
        return True


class BrokenSource(HealthySource):
    def ping(self):
        raise TimeoutError("source timeout")


class HealthyTarget:
    def __init__(self, config):
        self.config = config

    def ping(self):
        return None


def test_events_stream_starts_with_snapshot(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", HealthySource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", HealthyTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    response = app.test_client().get("/api/events?once=true")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert body.startswith("event: snapshot\n")
    assert "\ndata: " in body

    payload = _event_payload(body)
    assert payload["health"]["status"] == "ok"
    assert payload["tasks"][0]["name"] == "默认自动同步"
    assert payload["executions"] == []
    assert payload["active_execution"] is None
    assert "server_time" in payload


def test_events_defaults_to_single_snapshot(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", HealthySource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", HealthyTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    response = app.test_client().get("/api/events")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert body.count("event: snapshot") == 1
    assert "event: heartbeat" not in body


def test_events_snapshot_degrades_when_health_check_fails(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", BrokenSource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", HealthyTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    response = app.test_client().get("/api/events?once=true")
    payload = _event_payload(response.get_data(as_text=True))

    assert response.status_code == 200
    assert payload["health"]["status"] == "degraded"
    assert payload["health"]["source"]["error"] == "source timeout"


def _event_payload(body: str) -> dict:
    data_line = next(line for line in body.splitlines() if line.startswith("data: "))
    return json.loads(data_line.removeprefix("data: "))
