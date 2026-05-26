from edc_extractor.scheduler_store import SchedulerStore


def test_scheduler_store_migrations_are_idempotent(tmp_path):
    db_path = tmp_path / "scheduler.db"

    first = SchedulerStore(db_path)
    second = SchedulerStore(db_path)

    assert first.list_executions() == []
    assert second.apply_migrations() == []


def test_scheduler_store_creates_and_updates_default_task(tmp_path):
    store = SchedulerStore(tmp_path / "scheduler.db")

    task = store.ensure_default_task(
        name="默认自动同步",
        cron_expression="*/10 * * * *",
        time_window_minutes=60,
        delay_minutes=10,
        enabled=True,
    )
    same_task = store.ensure_default_task(
        name="默认自动同步",
        cron_expression="*/5 * * * *",
        time_window_minutes=30,
        delay_minutes=5,
        enabled=False,
    )

    assert task["id"] == same_task["id"]
    assert task["cron_expression"] == "*/10 * * * *"

    updated = store.update_scheduled_task(
        task["id"],
        cron_expression="*/5 * * * *",
        time_window_minutes=30,
        delay_minutes=5,
        enabled=False,
    )

    assert updated["cron_expression"] == "*/5 * * * *"
    assert updated["time_window_minutes"] == 30
    assert updated["delay_minutes"] == 5
    assert updated["enabled"] == 0
