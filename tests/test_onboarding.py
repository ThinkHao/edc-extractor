from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from edc_extractor.entity_onboarding import SourceEntityCandidate
from edc_extractor.onboarding import EDCOnboardingMonitor, _notification_due
from edc_extractor.sync_engine import EDCEntity, SyncConfig, SyncSummary


class FakeNotifier:
    enabled = True

    def __init__(self):
        self.messages = []

    def send(self, title, text, now=None):
        self.messages.append((title, text))
        return True


class FakeSource:
    def __init__(self, first_seen):
        self.first_seen = first_seen

    def discover_entity_candidates(self, start_time, end_time, limit):
        return [
            SourceEntityCandidate(
                edc_name="BJ-new",
                sn="SN1",
                first_seen_create_time=self.first_seen,
                latest_create_time=self.first_seen + timedelta(hours=1),
                record_count=2,
            )
        ]

    def get_entity_time_bounds(self, edc_name, sn):
        return self.first_seen, self.first_seen + timedelta(hours=1)


class FakeTarget:
    def __init__(self):
        self.entities = [EDCEntity(7, "BJ-new", "SN1", "BJ-new", "北京", "阿里")]
        self.candidates = []
        self.statuses = {}

    def load_entity_mappings(self):
        return {}

    def upsert_entity_candidates(self, candidates, configured_keys=None):
        self.candidates = [
            {
                "id": 1,
                "edc_name": c.edc_name,
                "sn": c.sn,
                "first_seen_at": c.first_seen_create_time,
                "latest_seen_at": c.latest_create_time,
                "record_count": c.record_count,
                "status": "pending",
                "last_notified_level": 0,
                "discovered_at": datetime(2026, 8, 13, 8, 0),
            }
            for c in candidates
        ]

    def list_entity_candidates(self, statuses=None, limit=5000, entity_keys=None):
        return [c for c in self.candidates if not statuses or c["status"] in statuses]

    def load_enabled_entities(self):
        return self.entities

    def mark_candidate_backfill_pending(self, candidate_id, entity_id, start_time, end_time):
        self.statuses[candidate_id] = ("backfill_pending", start_time, end_time)
        return True

    def claim_candidate_backfill(self, candidate_id):
        self.statuses[candidate_id] = (*self.statuses[candidate_id][1:], "backfilling")
        return True

    def complete_candidate_backfill(self, candidate_id, rows_written):
        self.statuses[candidate_id] = ("ready", rows_written)

    def fail_candidate_backfill(self, candidate_id, error):
        self.statuses[candidate_id] = ("failed", error)

    def record_candidate_notification(self, candidate_id, level, sent_at):
        self.candidates[0]["last_notified_level"] = level


class FakeEngine:
    def __init__(self):
        self.calls = []

    def sync(self, start_time, end_time, entity_keys=None):
        self.calls.append((start_time, end_time, entity_keys))
        return SyncSummary(start_time, end_time, [])


def test_monitor_notifies_and_schedules_exact_entity_backfill():
    first_seen = datetime(2026, 8, 1, 0, 0)
    source = FakeSource(first_seen)
    target = FakeTarget()
    notifier = FakeNotifier()
    engine = FakeEngine()
    with ThreadPoolExecutor(max_workers=1) as executor:
        monitor = EDCOnboardingMonitor(
            source=source,
            target=target,
            engine=engine,
            notifier=notifier,
            backfill_executor=executor,
            sync_config=SyncConfig(chunk_hours=24),
        )
        result = monitor.run_cycle(datetime(2026, 8, 13, 10, 0))
        assert result["notifications_sent"] == 1
        assert notifier.messages
        assert target.statuses[1][0] == "ready"
        assert engine.calls
        assert engine.calls[0][2] == {("BJ-new", "SN1")}


def test_notification_is_at_most_once_per_24_hours():
    now = datetime(2026, 8, 13, 10, 0)
    assert _notification_due({"last_notified_at": None}, now)
    assert not _notification_due({"last_notified_at": now - timedelta(hours=23, minutes=59)}, now)
    assert _notification_due({"last_notified_at": now - timedelta(hours=24)}, now)
