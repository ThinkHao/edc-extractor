from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SourceEntityCandidate:
    edc_name: str
    sn: str
    latest_create_time: datetime
    record_count: int


def is_backup_edc_name(edc_name: str) -> bool:
    return "backup" in edc_name.lower()


def build_entity_payload(
    candidates: list[SourceEntityCandidate],
    configured_keys: set[tuple[str, str]],
) -> list[dict]:
    return [
        {
            "edc_name": candidate.edc_name,
            "sn": candidate.sn,
            "latest_create_time": candidate.latest_create_time.strftime("%Y-%m-%d %H:%M:%S"),
            "record_count": candidate.record_count,
            "is_backup": is_backup_edc_name(candidate.edc_name),
            "configured": (candidate.edc_name, candidate.sn) in configured_keys,
        }
        for candidate in candidates
    ]
