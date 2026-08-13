from datetime import datetime

from edc_extractor.config import NotificationConfig
from edc_extractor.notification import FeishuNotificationClient, _mention_text, _within_notification_window


def test_notification_window_is_09_to_18():
    assert _within_notification_window(datetime(2026, 8, 13, 9, 0), 9, 18)
    assert _within_notification_window(datetime(2026, 8, 13, 17, 59), 9, 18)
    assert not _within_notification_window(datetime(2026, 8, 13, 8, 59), 9, 18)
    assert not _within_notification_window(datetime(2026, 8, 13, 18, 0), 9, 18)


def test_mention_text_is_safe_when_open_id_is_missing():
    assert _mention_text("hello", "", "郝金鑫") == "hello"
    assert '<at user_id="ou_test">郝金鑫</at>' in _mention_text("hello", "ou_test", "郝金鑫")


def test_client_without_credentials_is_noop():
    client = FeishuNotificationClient(NotificationConfig(enabled=True, chat_id="oc_test"))
    assert client.enabled is False
    assert client.send("title", "body", datetime(2026, 8, 13, 10, 0)) is False


def test_app_client_builds_group_message_and_mentions_user(monkeypatch):
    client = FeishuNotificationClient(
        NotificationConfig(
            enabled=True,
            app_id="cli_test",
            app_secret="secret",
            chat_id="oc_test",
            mention_open_id="ou_test",
        )
    )
    calls = []

    def fake_post(url, payload, headers=None):
        calls.append((url, payload, headers or {}))
        if "/auth/" in url:
            return {"tenant_access_token": "token", "expire": 3600}
        return {"code": 0}

    monkeypatch.setattr(client, "_post_json", fake_post)
    assert client.send("title", "body", datetime(2026, 8, 13, 10, 0)) is True
    assert calls[1][2]["Authorization"] == "Bearer token"
    assert "ou_test" in calls[1][1]["content"]
    assert calls[1][1]["receive_id"] == "oc_test"
