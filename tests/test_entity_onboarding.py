from datetime import datetime

from edc_extractor.entity_onboarding import (
    ConfiguredEntityMapping,
    SourceEntityCandidate,
    build_entity_payload,
    is_backup_edc_name,
)


def test_backup_edc_name_detection_is_case_insensitive():
    assert is_backup_edc_name("TJ-Bilibili-backup")
    assert is_backup_edc_name("GD-BAIDU-BACKUP")
    assert not is_backup_edc_name("TJ-Bilibili")


def test_build_entity_payload_marks_configured_and_backup_candidates():
    candidates = [
        SourceEntityCandidate(
            edc_name="TJ-Bilibili-backup",
            sn="TJM1808960134",
            latest_create_time=datetime(2026, 5, 26, 10, 0, 0),
            record_count=12,
        ),
        SourceEntityCandidate(
            edc_name="TJ-Bilibili",
            sn="TJM1808960134",
            latest_create_time=datetime(2026, 5, 26, 10, 0, 0),
            record_count=10,
        ),
    ]

    payload = build_entity_payload(
        candidates,
        {
            ("TJ-Bilibili", "TJM1808960134"): ConfiguredEntityMapping(
                edc_name="TJ-Bilibili",
                sn="TJM1808960134",
                display_name="天津-Bilibili",
            region="天津",
            cp="bilibili",
            is_backup=False,
            enabled=True,
            remark="人工确认",
            entity_type="node",
            src_region="北京市",
            dst_region="天津市",
            )
        },
    )

    assert payload == [
        {
            "edc_name": "TJ-Bilibili-backup",
            "sn": "TJM1808960134",
            "latest_create_time": "2026-05-26 10:00:00",
            "record_count": 12,
            "is_backup": True,
            "configured": False,
            "duplicate_count": 0,
        },
        {
            "edc_name": "TJ-Bilibili",
            "sn": "TJM1808960134",
            "latest_create_time": "2026-05-26 10:00:00",
            "record_count": 10,
            "is_backup": False,
            "configured": True,
            "duplicate_count": 0,
            "display_name": "天津-Bilibili",
            "alias": None,
            "region": "天津",
            "cp": "bilibili",
            "entity_type": "node",
            "src_region": "北京市",
            "dst_region": "天津市",
            "enabled": True,
            "remark": "人工确认",
        },
    ]


def test_build_entity_payload_counts_same_name_entries_across_history_and_discovery():
    candidates = [
        SourceEntityCandidate(
            edc_name="BJ-ali-01",
            sn="NEW-SN",
            latest_create_time=datetime(2026, 5, 26, 10, 0, 0),
            record_count=12,
        ),
        SourceEntityCandidate(
            edc_name="BJ-ali-01",
            sn="NEWER-SN",
            latest_create_time=datetime(2026, 5, 26, 10, 5, 0),
            record_count=10,
        ),
    ]
    configured = {
        ("BJ-ali-01", "OLD-SN"): ConfiguredEntityMapping(
            edc_name="BJ-ali-01",
            sn="OLD-SN",
            display_name="BJ-ali-01",
            region="北京市",
            cp="阿里",
            is_backup=False,
            enabled=False,
            remark="旧条目",
        )
    }

    payload = build_entity_payload(candidates, configured)

    assert [item["duplicate_count"] for item in payload] == [2, 2]

    history_payload = build_entity_payload(candidates, configured, include_configured_history=True)

    assert len(history_payload) == 3
    assert history_payload[-1]["history_only"] is True
    assert history_payload[-1]["latest_create_time"] == "-"
