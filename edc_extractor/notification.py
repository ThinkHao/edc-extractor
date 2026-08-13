from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import NotificationConfig

LOGGER = logging.getLogger(__name__)


class NotificationClient:
    enabled = False

    def send(self, title: str, text: str, now: datetime | None = None) -> bool:
        return False


class FeishuNotificationClient(NotificationClient):
    def __init__(self, config: NotificationConfig, timeout_seconds: int = 5):
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.enabled = bool(
            config.enabled
            and (
                config.webhook_url
                or (config.app_id and config.app_secret and config.chat_id)
            )
        )
        self._token = ""
        self._token_expire_at = 0.0
        self._token_lock = threading.Lock()

    def send(self, title: str, text: str, now: datetime | None = None) -> bool:
        if not self.enabled:
            return False
        current = now or datetime.now()
        if not _within_notification_window(current, self.config.start_hour, self.config.end_hour):
            return False
        message = f"【EDC录入与补录告警】{title}\n{text}"
        try:
            if self.config.webhook_url:
                self._send_webhook(message)
            else:
                self._send_app(message)
            return True
        except Exception as exc:
            LOGGER.warning("发送飞书 EDC 告警失败: %s", exc)
            return False

    def _send_webhook(self, message: str) -> None:
        content = {"text": _mention_text(message, self.config.mention_open_id, self.config.mention_name)}
        self._post_json(self.config.webhook_url, {"msg_type": "text", "content": content})

    def _send_app(self, message: str) -> None:
        token = self._get_tenant_access_token()
        url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
        payload = {
            "receive_id": self.config.chat_id,
            "msg_type": "text",
            "content": json.dumps(
                {"text": _mention_text(message, self.config.mention_open_id, self.config.mention_name)},
                ensure_ascii=False,
            ),
        }
        self._post_json(url, payload, headers={"Authorization": f"Bearer {token}"})

    def _get_tenant_access_token(self) -> str:
        import time

        with self._token_lock:
            if self._token and time.time() < self._token_expire_at - 60:
                return self._token
            payload = {"app_id": self.config.app_id, "app_secret": self.config.app_secret}
            response = self._post_json(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                payload,
            )
            token = str(response.get("tenant_access_token") or "")
            if not token:
                raise RuntimeError("飞书 token 响应缺少 tenant_access_token")
            self._token = token
            self._token_expire_at = time.time() + int(response.get("expire", 3600))
            return token

    def _post_json(self, url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"网络错误: {exc.reason}") from exc
        if not raw:
            return {}
        result = json.loads(raw)
        if int(result.get("code", 0)) != 0:
            raise RuntimeError(f"飞书接口错误 code={result.get('code')} msg={result.get('msg')}")
        return result


def _mention_text(message: str, open_id: str, name: str) -> str:
    if not open_id:
        return message
    return f'<at user_id="{open_id}">{name or open_id}</at> {message}'


def _within_notification_window(now: datetime, start_hour: int, end_hour: int) -> bool:
    minutes = now.hour * 60 + now.minute
    start = max(0, min(23, start_hour)) * 60
    end = max(0, min(24, end_hour)) * 60
    if start == end:
        return True
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end

