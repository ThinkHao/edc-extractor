from datetime import datetime, timedelta

import pytest

from edc_extractor.sync_engine import (
    EDCEntity,
    SyncConfig,
    SyncEngine,
    build_time_chunks,
)


def test_build_time_chunks_uses_left_closed_right_open_ranges():
    start = datetime(2026, 5, 1, 0, 0, 0)
    end = datetime(2026, 5, 1, 13, 0, 0)

    chunks = build_time_chunks(start, end, chunk_hours=6)

    assert chunks == [
        (datetime(2026, 5, 1, 0, 0, 0), datetime(2026, 5, 1, 6, 0, 0)),
        (datetime(2026, 5, 1, 6, 0, 0), datetime(2026, 5, 1, 12, 0, 0)),
        (datetime(2026, 5, 1, 12, 0, 0), datetime(2026, 5, 1, 13, 0, 0)),
    ]


def test_build_time_chunks_rejects_invalid_window():
    start = datetime(2026, 5, 1, 0, 0, 0)
    with pytest.raises(ValueError, match="start_time must be earlier"):
        build_time_chunks(start, start, chunk_hours=6)


def test_sync_engine_aggregates_source_rows_and_writes_idempotently():
    start = datetime(2026, 5, 1, 0, 0, 0)
    end = start + timedelta(hours=1)
    source_rows = [
        {
            "create_time": start,
            "edc_name": "TJ-Bilibili-backup",
            "sn": "TJM1808960134",
            "service_size": 100,
            "cache_size": 20,
            "record_count": 1,
        },
        {
            "create_time": start,
            "edc_name": "TJ-Bilibili-backup",
            "sn": "TJM1808960134",
            "service_size": 200,
            "cache_size": 30,
            "record_count": 2,
        },
    ]
    source = FakeSource(source_rows)
    target = FakeTarget(
        [
            EDCEntity(
                id=10,
                edc_name="TJ-Bilibili-backup",
                sn="TJM1808960134",
                display_name="TJ-Bilibili",
                region="天津",
                cp="bilibili",
            )
        ]
    )
    engine = SyncEngine(source, target, SyncConfig(chunk_hours=6, edc_name_batch_size=200))

    first = engine.sync(start, end)
    second = engine.sync(start, end)

    assert first.rows_read == 2
    assert first.rows_written == 1
    assert first.unmapped_count == 0
    assert second.rows_written == 1
    assert target.rows[(start, 10)] == {
        "bucket_5m": start,
        "entity_id": 10,
        "service_size": 300,
        "cache_size": 50,
        "record_count": 3,
    }


def test_sync_engine_tracks_unmapped_rows_without_writing_them():
    start = datetime(2026, 5, 1, 0, 0, 0)
    end = start + timedelta(hours=1)
    source = FakeSource(
        [
                {
                    "create_time": start,
                    "edc_name": "TJ-Bilibili-backup",
                    "sn": "NOPE",
                    "service_size": 100,
                    "cache_size": 20,
                    "record_count": 1,
                }
            ]
        )
    target = FakeTarget(
        [
            EDCEntity(
                id=10,
                edc_name="TJ-Bilibili-backup",
                sn="TJM1808960134",
                display_name="TJ-Bilibili",
                region="天津",
                cp="bilibili",
            ),
            EDCEntity(
                id=11,
                edc_name="TJ-Bilibili-backup",
                sn="OTHER-SN",
                display_name="TJ-Bilibili-2",
                region="天津",
                cp="bilibili",
            ),
        ]
    )
    engine = SyncEngine(source, target, SyncConfig(chunk_hours=6, edc_name_batch_size=200))

    summary = engine.sync(start, end)

    assert summary.rows_read == 1
    assert summary.rows_written == 0
    assert summary.unmapped_count == 1
    assert target.rows == {}


def test_sync_engine_resolves_sn_change_when_edc_name_is_unique():
    start = datetime(2026, 5, 1, 0, 0, 0)
    source = FakeSource(
        [
            {
                "create_time": start,
                "edc_name": "cs-TJ-Trunk10-QHD",
                "sn": "OLD-SN",
                "service_size": 100,
                "cache_size": 20,
                "record_count": 1,
            }
        ]
    )
    target = FakeTarget(
        [
            EDCEntity(
                id=20,
                edc_name="cs-TJ-Trunk10-QHD",
                sn="NEW-SN",
                display_name="cs-TJ-Trunk10-QHD",
                region="cs",
                cp="TJ",
            )
        ]
    )
    engine = SyncEngine(source, target, SyncConfig(chunk_hours=6, edc_name_batch_size=200))

    summary = engine.sync(start, start + timedelta(hours=1))

    assert summary.rows_read == 1
    assert summary.rows_written == 1
    assert summary.unmapped_count == 0
    assert target.rows[(start, 20)]["service_size"] == 100


def test_sync_engine_keeps_sn_strict_when_edc_name_has_multiple_mappings():
    start = datetime(2026, 5, 1, 0, 0, 0)
    source = FakeSource(
        [
            {
                "create_time": start,
                "edc_name": "cs-tz-bj",
                "sn": "UNKNOWN-SN",
                "service_size": 100,
                "cache_size": 20,
                "record_count": 1,
            }
        ]
    )
    target = FakeTarget(
        [
            EDCEntity(id=21, edc_name="cs-tz-bj", sn="SN-A", display_name="cs-tz-bj-a", region="cs", cp="tz"),
            EDCEntity(id=22, edc_name="cs-tz-bj", sn="SN-B", display_name="cs-tz-bj-b", region="cs", cp="tz"),
        ]
    )
    engine = SyncEngine(source, target, SyncConfig(chunk_hours=6, edc_name_batch_size=200))

    summary = engine.sync(start, start + timedelta(hours=1))

    assert summary.rows_read == 1
    assert summary.rows_written == 0
    assert summary.unmapped_count == 1


def test_sync_engine_reuses_task_sessions_for_all_chunks():
    start = datetime(2026, 5, 1, 0, 0, 0)
    end = start + timedelta(hours=13)
    source = SessionFakeSource(
        [
            {
                "create_time": start,
                "edc_name": "TJ-Bilibili-backup",
                "sn": "TJM1808960134",
                "service_size": 100,
                "cache_size": 20,
                "record_count": 1,
            }
        ]
    )
    target = SessionFakeTarget(
        [
            EDCEntity(
                id=10,
                edc_name="TJ-Bilibili-backup",
                sn="TJM1808960134",
                display_name="TJ-Bilibili",
                region="天津",
                cp="bilibili",
            )
        ]
    )
    engine = SyncEngine(source, target, SyncConfig(chunk_hours=6, edc_name_batch_size=200))

    summary = engine.sync(start, end)

    assert summary.rows_read == 1
    assert len(source.queries) == 3
    assert source.open_count == 1
    assert source.close_count == 1
    assert target.open_count == 1
    assert target.close_count == 1


class FakeSource:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def fetch_aggregated_rows(self, start_time, end_time, edc_names):
        self.queries.append((start_time, end_time, tuple(edc_names)))
        for row in self.rows:
            if start_time <= row["create_time"] < end_time and row["edc_name"] in edc_names:
                yield row


class FakeTarget:
    def __init__(self, entities):
        self.entities = entities
        self.rows = {}

    def load_enabled_entities(self):
        return self.entities

    def upsert_traffic_rows(self, rows):
        count = 0
        for row in rows:
            self.rows[(row["bucket_5m"], row["entity_id"])] = row
            count += 1
        return count


class SessionFakeSource(FakeSource):
    def __init__(self, rows):
        super().__init__(rows)
        self.open_count = 0
        self.close_count = 0

    def open_session(self):
        return self

    def __enter__(self):
        self.open_count += 1
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close_count += 1


class SessionFakeTarget(FakeTarget):
    def __init__(self, entities):
        super().__init__(entities)
        self.open_count = 0
        self.close_count = 0

    def open_session(self):
        return self

    def __enter__(self):
        self.open_count += 1
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close_count += 1
