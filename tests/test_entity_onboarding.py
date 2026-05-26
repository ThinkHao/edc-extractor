from datetime import datetime

from edc_extractor.entity_onboarding import (
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

    payload = build_entity_payload(candidates, {("TJ-Bilibili", "TJM1808960134")})

    assert payload == [
        {
            "edc_name": "TJ-Bilibili-backup",
            "sn": "TJM1808960134",
            "latest_create_time": "2026-05-26 10:00:00",
            "record_count": 12,
            "is_backup": True,
            "configured": False,
        },
        {
            "edc_name": "TJ-Bilibili",
            "sn": "TJM1808960134",
            "latest_create_time": "2026-05-26 10:00:00",
            "record_count": 10,
            "is_backup": False,
            "configured": True,
        },
    ]
