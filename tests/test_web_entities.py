from pathlib import Path
from datetime import datetime

import edc_extractor.web as web_module
from edc_extractor.entity_onboarding import ConfiguredEntityMapping, SourceEntityCandidate


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

    def load_entity_mappings(self):
        return {}


class ConfiguredSource:
    def __init__(self, config):
        self.config = config

    def discover_entity_candidates(self, start_time, end_time, limit):
        return [
            SourceEntityCandidate(
                edc_name="BJ-ali-01",
                sn="SN1",
                latest_create_time=datetime(2026, 6, 3, 16, 45, 0),
                record_count=12,
            )
        ]


class ConfiguredTarget:
    def __init__(self, config):
        self.config = config

    def load_entity_mappings(self):
        return {
            ("BJ-ali-01", "SN1"): ConfiguredEntityMapping(
                edc_name="BJ-ali-01",
                sn="SN1",
                display_name="BJ-ali-01",
                region="北京市",
                cp="阿里",
                is_backup=False,
                enabled=True,
                remark="人工录入",
            )
        }


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


def test_source_entities_returns_configured_manual_fields(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", ConfiguredSource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", ConfiguredTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    response = app.test_client().get(
        "/api/source/entities?start_time=2026-06-01%2000:00:00&end_time=2026-06-01%2001:00:00&limit=10"
    )
    item = response.get_json()["items"][0]

    assert response.status_code == 200
    assert item["configured"] is True
    assert item["region"] == "北京市"
    assert item["cp"] == "阿里"
    assert item["remark"] == "人工录入"
