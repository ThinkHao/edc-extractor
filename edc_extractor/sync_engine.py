from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from contextlib import nullcontext
from itertools import islice
from time import monotonic
from typing import Callable, Iterable, Iterator, Protocol

ANY_SN = "__any_sn__"


@dataclass(frozen=True)
class EDCEntity:
    id: int
    edc_name: str
    sn: str
    display_name: str
    region: str
    cp: str
    is_backup: bool = False


@dataclass(frozen=True)
class SyncConfig:
    chunk_hours: int = 6
    edc_name_batch_size: int = 200
    insert_batch_size: int = 5000
    max_manual_range_days: int = 31
    allow_large_manual_range: bool = False


@dataclass
class SyncChunkSummary:
    start_time: datetime
    end_time: datetime
    rows_read: int = 0
    rows_written: int = 0
    unmapped_count: int = 0
    duration_ms: int = 0
    error: str | None = None


@dataclass
class SyncSummary:
    start_time: datetime
    end_time: datetime
    chunks: list[SyncChunkSummary]

    @property
    def rows_read(self) -> int:
        return sum(chunk.rows_read for chunk in self.chunks)

    @property
    def rows_written(self) -> int:
        return sum(chunk.rows_written for chunk in self.chunks)

    @property
    def unmapped_count(self) -> int:
        return sum(chunk.unmapped_count for chunk in self.chunks)

    @property
    def duration_ms(self) -> int:
        return sum(chunk.duration_ms for chunk in self.chunks)


class EDCSource(Protocol):
    def fetch_aggregated_rows(self, start_time: datetime, end_time: datetime, edc_names: list[str]) -> Iterable[dict]:
        ...


class EDCTarget(Protocol):
    def load_enabled_entities(self) -> list[EDCEntity]:
        ...

    def upsert_traffic_rows(self, rows: list[dict]) -> int:
        ...


def build_time_chunks(start_time: datetime, end_time: datetime, chunk_hours: int) -> list[tuple[datetime, datetime]]:
    if not start_time < end_time:
        raise ValueError("start_time must be earlier than end_time")
    if chunk_hours <= 0:
        raise ValueError("chunk_hours must be positive")
    step = timedelta(hours=chunk_hours)
    chunks: list[tuple[datetime, datetime]] = []
    cursor = start_time
    while cursor < end_time:
        next_cursor = min(cursor + step, end_time)
        chunks.append((cursor, next_cursor))
        cursor = next_cursor
    return chunks


def batched(values: Iterable[str], size: int) -> Iterator[list[str]]:
    iterator = iter(values)
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            return
        yield batch


class SyncEngine:
    def __init__(self, source: EDCSource, target: EDCTarget, config: SyncConfig | None = None):
        self.source = source
        self.target = target
        self.config = config or SyncConfig()

    def sync(
        self,
        start_time: datetime,
        end_time: datetime,
        progress_callback: Callable[[int, int, SyncChunkSummary, SyncSummary], None] | None = None,
    ) -> SyncSummary:
        self._validate_range(start_time, end_time)
        chunks: list[SyncChunkSummary] = []
        with self._task_session(self.source) as source, self._task_session(self.target) as target:
            entities = target.load_enabled_entities()
            entity_map = self._build_entity_map(entities)
            edc_names = sorted({entity.edc_name for entity in entities})
            time_chunks = build_time_chunks(start_time, end_time, self.config.chunk_hours)
            total_chunks = len(time_chunks)

            for index, (chunk_start, chunk_end) in enumerate(time_chunks, 1):
                chunk_summary = self._sync_chunk(source, target, chunk_start, chunk_end, edc_names, entity_map)
                chunks.append(chunk_summary)
                if progress_callback:
                    progress_callback(
                        index,
                        total_chunks,
                        chunk_summary,
                        SyncSummary(start_time=start_time, end_time=end_time, chunks=list(chunks)),
                    )

        return SyncSummary(start_time=start_time, end_time=end_time, chunks=chunks)

    def _validate_range(self, start_time: datetime, end_time: datetime) -> None:
        if not start_time < end_time:
            raise ValueError("start_time must be earlier than end_time")
        if not self.config.allow_large_manual_range:
            max_span = timedelta(days=self.config.max_manual_range_days)
            if end_time - start_time > max_span:
                raise ValueError(f"time range exceeds {self.config.max_manual_range_days} days")

    def _sync_chunk(
        self,
        source: EDCSource,
        target: EDCTarget,
        start_time: datetime,
        end_time: datetime,
        edc_names: list[str],
        entity_map: dict[tuple[str, str], EDCEntity],
    ) -> SyncChunkSummary:
        started = monotonic()
        summary = SyncChunkSummary(start_time=start_time, end_time=end_time)
        aggregate: dict[tuple[datetime, int], dict] = {}

        for name_batch in batched(edc_names, self.config.edc_name_batch_size):
            for source_row in source.fetch_aggregated_rows(start_time, end_time, name_batch):
                summary.rows_read += 1
                entity = self._resolve_entity(entity_map, source_row)
                if entity is None:
                    summary.unmapped_count += 1
                    continue
                key = (source_row["create_time"], entity.id)
                row = aggregate.setdefault(
                    key,
                    {
                        "bucket_5m": source_row["create_time"],
                        "entity_id": entity.id,
                        "service_size": 0,
                        "cache_size": 0,
                        "record_count": 0,
                    },
                )
                row["service_size"] += int(source_row.get("service_size") or 0)
                row["cache_size"] += int(source_row.get("cache_size") or 0)
                row["record_count"] += int(source_row.get("record_count") or 0)

        rows = list(aggregate.values())
        for row_batch in self._row_batches(rows):
            summary.rows_written += target.upsert_traffic_rows(row_batch)

        summary.duration_ms = int((monotonic() - started) * 1000)
        return summary

    def _row_batches(self, rows: list[dict]) -> Iterator[list[dict]]:
        for i in range(0, len(rows), self.config.insert_batch_size):
            yield rows[i : i + self.config.insert_batch_size]

    @staticmethod
    def _task_session(adapter):
        opener = getattr(adapter, "open_session", None)
        if callable(opener):
            return opener()
        return nullcontext(adapter)

    @staticmethod
    def _build_entity_map(entities: list[EDCEntity]) -> dict[tuple[str, str], EDCEntity]:
        out: dict[tuple[str, str], EDCEntity] = {}
        by_name: dict[str, list[EDCEntity]] = {}
        for entity in entities:
            out[(entity.edc_name, entity.sn or "")] = entity
            by_name.setdefault(entity.edc_name, []).append(entity)
            if not entity.sn:
                out.setdefault((entity.edc_name, ""), entity)
        for edc_name, name_entities in by_name.items():
            unique_ids = {entity.id for entity in name_entities}
            if len(unique_ids) == 1:
                out[(edc_name, ANY_SN)] = name_entities[0]
        return out

    @staticmethod
    def _resolve_entity(entity_map: dict[tuple[str, str], EDCEntity], row: dict) -> EDCEntity | None:
        edc_name = str(row.get("edc_name") or "")
        sn = str(row.get("sn") or "")
        return entity_map.get((edc_name, sn)) or entity_map.get((edc_name, "")) or entity_map.get((edc_name, ANY_SN))
