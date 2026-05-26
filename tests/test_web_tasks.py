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


class DummySource:
    def __init__(self, config):
        self.config = config


class DummyTarget:
    def __init__(self, config):
        self.config = config


def test_task_api_lists_and_updates_default_task(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", DummySource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", DummyTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    client = app.test_client()

    payload = client.get("/api/tasks").get_json()
    task = payload["items"][0]

    assert task["name"] == "默认自动同步"
    assert task["cron_expression"] == "*/10 * * * *"
    assert task["enabled"] is True

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={
            "cron_expression": "*/5 * * * *",
            "time_window_minutes": 30,
            "delay_minutes": 5,
            "enabled": False,
        },
    )
    updated = response.get_json()

    assert response.status_code == 200
    assert updated["cron_expression"] == "*/5 * * * *"
    assert updated["time_window_minutes"] == 30
    assert updated["delay_minutes"] == 5
    assert updated["enabled"] is False


def test_task_api_rejects_invalid_cron(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", DummySource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", DummyTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    task = app.test_client().get("/api/tasks").get_json()["items"][0]

    response = app.test_client().patch(f"/api/tasks/{task['id']}", json={"cron_expression": "bad cron"})

    assert response.status_code == 400
