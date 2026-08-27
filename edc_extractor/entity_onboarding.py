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
    entity_id: int | None = None


def is_backup_edc_name(edc_name: str) -> bool:
    return "backup" in edc_name.lower()


def build_entity_payload(
    candidates: list[SourceEntityCandidate],
    configured_entities: Mapping[tuple[str, str], ConfiguredEntityMapping],
    *,
    include_configured_history: bool = False,
) -> list[dict]:
    keys_by_name: dict[str, set[tuple[str, str]]] = {}
    for key in configured_entities:
        keys_by_name.setdefault(key[0], set()).add((str(key[0]), str(key[1] or "")))
    for candidate in candidates:
        keys_by_name.setdefault(candidate.edc_name, set()).add((candidate.edc_name, candidate.sn or ""))

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
            "duplicate_count": max(0, len(keys_by_name.get(candidate.edc_name, set())) - 1),
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
            if configured.entity_id is not None:
                item["entity_id"] = configured.entity_id
        payload.append(item)

    if include_configured_history:
        candidate_keys = {(candidate.edc_name, candidate.sn or "") for candidate in candidates}
        candidate_names = {candidate.edc_name for candidate in candidates}
        for key, configured in configured_entities.items():
            normalized_key = (str(key[0]), str(key[1] or ""))
            if configured.edc_name not in candidate_names or normalized_key in candidate_keys:
                continue
            payload.append(
                {
                    "edc_name": configured.edc_name,
                    "sn": configured.sn,
                    "latest_create_time": "-",
                    "record_count": 0,
                    "is_backup": configured.is_backup,
                    "configured": True,
                    "duplicate_count": max(0, len(keys_by_name.get(configured.edc_name, set())) - 1),
                    "entity_id": configured.entity_id,
                    "display_name": configured.display_name,
                    "alias": configured.alias,
                    "region": configured.region,
                    "cp": configured.cp,
                    "entity_type": configured.entity_type,
                    "src_region": configured.src_region,
                    "dst_region": configured.dst_region,
                    "enabled": configured.enabled,
                    "remark": configured.remark,
                    "history_only": True,
                }
            )
    return payload
