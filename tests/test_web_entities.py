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


class FailingSource:
    def __init__(self, config):
        self.config = config

    def discover_entity_candidates(self, start_time, end_time, limit):
        raise RuntimeError("source timeout")


class DummyTarget:
    def __init__(self, config):
        self.config = config

    def load_entity_keys(self):
        return set()


def test_source_entities_returns_json_when_discovery_fails(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", FailingSource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", DummyTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    response = app.test_client().get(
        "/api/source/entities?start_time=2026-06-01%2000:00:00&end_time=2026-06-01%2001:00:00&limit=10"
    )
    payload = response.get_json()

    assert response.status_code == 500
    assert payload["error"] == "源端 EDC 发现失败: source timeout"
