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


class DuplicateConfiguredTarget:
    def __init__(self, config):
        self.config = config

    def load_entity_mappings(self):
        return {
            ("BJ-ali-01", "OLD-SN"): ConfiguredEntityMapping(
                edc_name="BJ-ali-01",
                sn="OLD-SN",
                display_name="BJ-ali-01",
                region="北京市",
                cp="阿里",
                is_backup=False,
                enabled=False,
                remark="旧条目",
                entity_id=7,
            )
        }


class DuplicateConfiguredSource:
    def __init__(self, config):
        self.config = config

    def discover_entity_candidates(self, start_time, end_time, limit):
        return [
            SourceEntityCandidate(
                edc_name="BJ-ali-01",
                sn="NEW-SN",
                latest_create_time=datetime(2026, 6, 3, 16, 45, 0),
                record_count=12,
            )
        ]


class DisabledConfiguredTarget:
    def __init__(self, config):
        self.config = config

    def load_entity_mappings(self):
        return {
            ("BJ-ali-01", "SN1"): ConfiguredEntityMapping(
                edc_name="BJ-ali-01",
                sn="SN1",
                display_name="BJ-ali-01",
                region="天津市",
                cp="bilibili",
                is_backup=False,
                enabled=False,
                remark="已停用",
            )
        }


class EntityWriteTarget:
    def __init__(self, config):
        self.config = config
        self.metadata_calls = []

    def upsert_entities(self, rows):
        return len(rows)

    def entity_ids_for_keys(self, entity_keys):
        return [7]

    def get_entity_traffic_bounds(self, entity_ids):
        return datetime(2026, 6, 1), datetime(2026, 6, 2)

    def reconcile_traffic_metadata(self, entity_ids, progress_callback=None):
        self.metadata_calls.append(entity_ids)
        return {"kind": "metadata_reconcile", "total_entities": 1, "rows_updated": 0}


class StatusTarget:
    def __init__(self, config):
        self.config = config
        self.items = [
            {
                "id": 7,
                "edc_name": "BJ-ali-01",
                "sn": "SN1",
                "display_name": "BJ-ali-01",
                "alias": None,
                "region": "北京市",
                "cp": "阿里",
                "entity_type": "node",
                "is_backup": 0,
                "enabled": 0,
                "remark": "已停用",
                "created_at": datetime(2026, 6, 1, 0, 0),
                "updated_at": datetime(2026, 6, 1, 0, 0),
            }
        ]

    def list_entity_records(self):
        return self.items

    def set_entity_enabled(self, entity_id, enabled, *, operator, source_ip):
        for item in self.items:
            if item["id"] == entity_id:
                item["enabled"] = enabled
                return item
        return None


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


def test_source_entities_includes_history_rows_for_duplicate_group(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", DuplicateConfiguredSource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", DuplicateConfiguredTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    response = app.test_client().get(
        "/api/source/entities?start_time=2026-06-01%2000:00:00&end_time=2026-06-01%2001:00:00&limit=10&only_unconfigured=false"
    )
    items = response.get_json()["items"]

    assert response.status_code == 200
    assert len(items) == 2
    assert {item["sn"] for item in items} == {"NEW-SN", "OLD-SN"}
    assert items[1]["history_only"] is True
    assert all(item["duplicate_count"] == 1 for item in items)


def test_source_entities_keeps_disabled_mappings_visible_for_duplicate_resolution(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", ConfiguredSource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", DisabledConfiguredTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    response = app.test_client().get(
        "/api/source/entities?start_time=2026-06-01%2000:00:00&end_time=2026-06-01%2001:00:00&limit=10"
    )

    assert response.status_code == 200
    items = response.get_json()["items"]
    assert len(items) == 1
    assert items[0]["configured"] is True
    assert items[0]["enabled"] is False
    assert items[0]["duplicate_count"] == 0


def test_create_entities_schedules_metadata_reconcile(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", FailingSource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", EntityWriteTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    response = app.test_client().post(
        "/api/entities",
        json={
            "items": [
                {
                    "edc_name": "BJ-ali-01",
                    "sn": "SN1",
                    "display_name": "BJ-ali-01",
                    "region": "北京市",
                    "cp": "阿里",
                    "entity_type": "node",
                }
            ]
        },
    )
    payload = response.get_json()

    assert response.status_code == 202
    assert payload["metadata_sync_status"] == "scheduled"
    assert payload["metadata_sync_execution_id"]


def test_configured_entities_includes_disabled_rows(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", FailingSource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", StatusTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    response = app.test_client().get("/api/entities/configured")

    assert response.status_code == 200
    item = response.get_json()["items"][0]
    assert item["id"] == 7
    assert item["enabled"] is False
    assert item["updated_at"] == "2026-06-01 00:00:00"


def test_update_entity_enabled_validates_and_updates(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(web_module, "MySQLEDCSource", FailingSource)
    monkeypatch.setattr(web_module, "MySQLEDCTarget", StatusTarget)

    app = web_module.create_app(config_path, start_scheduler=False)
    client = app.test_client()
    response = client.patch("/api/entities/7/enabled", json={"enabled": True})
    invalid = client.patch("/api/entities/7/enabled", json={"enabled": "maybe"})
    missing = client.patch("/api/entities/99/enabled", json={"enabled": True})

    assert response.status_code == 200
    assert response.get_json()["item"]["enabled"] is True
    assert invalid.status_code == 400
    assert missing.status_code == 404
