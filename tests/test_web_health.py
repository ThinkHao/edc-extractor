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


class HealthyTarget:
    def __init__(self, config):
        self.config = config

    def ping(self):
        return None


def test_health_reports_source_and_target_connectivity(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", HealthySource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", HealthyTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    response = app.test_client().get("/health")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["source"]["connected"] is True
    assert payload["source"]["database"] == "cloud"
    assert payload["target"]["connected"] is True
    assert payload["target"]["database"] == "nfa"
    assert payload["recommended_source_index"] is True


def test_health_degrades_when_source_cannot_connect(tmp_path, monkeypatch):
    class BrokenSource(HealthySource):
        def ping(self):
            raise TimeoutError("source timeout")

        def has_recommended_index(self):
            raise AssertionError("index should not be checked when source ping fails")

    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", BrokenSource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", HealthyTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    payload = app.test_client().get("/health").get_json()

    assert payload["status"] == "degraded"
    assert payload["source"]["connected"] is False
    assert payload["source"]["error"] == "source timeout"
    assert payload["target"]["connected"] is True
    assert payload["recommended_source_index"] is False


def test_sync_error_identifies_source_host():
    message = web_module._describe_sync_error(
        Exception("2003: Can't connect to MySQL server on 'source.example:3306' (timed out)"),
        source_host="source.example",
        target_host="target.example",
    )

    assert message.startswith("源库连接或查询失败")
