from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping


ENTITY_TYPES = ("node", "transmission")


@dataclass(frozen=True)
class SourceEntityCandidate:
    edc_name: str
    sn: str
    latest_create_time: datetime
    record_count: int
    first_seen_create_time: datetime | None = None


@dataclass(frozen=True)
class ConfiguredEntityMapping:
    edc_name: str
    sn: str
    display_name: str
    region: str
    cp: str
    is_backup: bool
    enabled: bool
    remark: str
    alias: str | None = None
    entity_type: str | None = None
    src_region: str | None = None
    dst_region: str | None = None


def is_backup_edc_name(edc_name: str) -> bool:
    return "backup" in edc_name.lower()


def build_entity_payload(
    candidates: list[SourceEntityCandidate],
    configured_entities: Mapping[tuple[str, str], ConfiguredEntityMapping],
) -> list[dict]:
    payload = []
    for candidate in candidates:
        key = (candidate.edc_name, candidate.sn)
        configured = configured_entities.get(key)
        item = {
            "edc_name": candidate.edc_name,
            "sn": candidate.sn,
            "latest_create_time": candidate.latest_create_time.strftime("%Y-%m-%d %H:%M:%S"),
            "record_count": candidate.record_count,
            "is_backup": configured.is_backup if configured else is_backup_edc_name(candidate.edc_name),
            "configured": configured is not None,
        }
        if candidate.first_seen_create_time:
            item["first_seen_create_time"] = candidate.first_seen_create_time.strftime("%Y-%m-%d %H:%M:%S")
        if configured:
            item.update(
                {
                    "display_name": configured.display_name,
                    "alias": configured.alias,
                    "region": configured.region,
                    "cp": configured.cp,
                    "entity_type": configured.entity_type,
                    "src_region": configured.src_region,
                    "dst_region": configured.dst_region,
                    "enabled": configured.enabled,
                    "remark": configured.remark,
                }
            )
        payload.append(item)
    return payload
