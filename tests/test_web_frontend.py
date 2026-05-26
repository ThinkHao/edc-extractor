from pathlib import Path

import edc_extractor.web as web_module


def write_config(path: Path) -> None:
    path.write_text(
        """
[db_source]
host = localhost
port = 3306
user = root
password =
database = cloud
table = edc_data

[db_target]
host = localhost
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


def test_index_returns_clear_message_when_frontend_is_not_built(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setattr(web_module, "FRONTEND_DIST_DIR", tmp_path / "missing-dist")
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))

    app = web_module.create_app(config_path, start_scheduler=False)
    response = app.test_client().get("/")

    assert response.status_code == 503
    assert "前端尚未构建" in response.get_data(as_text=True)


def test_index_serves_built_frontend(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    write_config(config_path)
    monkeypatch.setenv("EDC_SCHEDULER_DB", str(tmp_path / "scheduler.db"))
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<!doctype html><title>EDC Console</title>", encoding="utf-8")
    monkeypatch.setattr(web_module, "FRONTEND_DIST_DIR", dist_dir)

    app = web_module.create_app(config_path, start_scheduler=False)
    response = app.test_client().get("/")

    assert response.status_code == 200
    assert "EDC Console" in response.get_data(as_text=True)
