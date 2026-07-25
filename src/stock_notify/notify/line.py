"""LINE 推播傳輸層。

只負責把一段文字送出去，不參與訊息內容的組裝（見 `formatting`）。

未設定憑證時使用 `LoggingNotifier`：完整跑完流程但只把訊息寫進 log，
供本機驗證使用，不會對任何人發出通知。
"""

from __future__ import annotations

import logging
from typing import Protocol

import requests

from stock_notify.config import HTTP_TIMEOUT_SECONDS, LINE_PUSH_API_URL

logger = logging.getLogger(__name__)


class NotificationError(Exception):
    """通知發送失敗。"""


class Notifier(Protocol):
    def send(self, message: str) -> None: ...


class LoggingNotifier:
    """把訊息寫進 log 而不實際發送。"""

    def send(self, message: str) -> None:
        logger.info("（未設定 LINE 憑證，以下為原本要發送的訊息）\n%s", message)


class LineNotifier:
    """透過 LINE Messaging API 推播訊息。

    `user_id` 可以是個人 (U 開頭)、群組 (C 開頭) 或聊天室 (R 開頭) 的 ID。
    """

    def __init__(self, token: str, user_id: str) -> None:
        self._user_id = user_id
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    def send(self, message: str) -> None:
        """Raises: NotificationError: 網路錯誤或 API 回傳非 200。"""
        payload = {
            "to": self._user_id,
            "messages": [{"type": "text", "text": message}],
        }

        try:
            response = self._session.post(
                LINE_PUSH_API_URL, json=payload, timeout=HTTP_TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            raise NotificationError(f"LINE 推播網路錯誤: {exc}") from exc

        if response.status_code != 200:
            raise NotificationError(
                f"LINE 推播失敗，狀態碼 {response.status_code}: {response.text}"
            )

        logger.info("LINE 通知已發送")


def create_notifier(token: str | None, user_id: str | None) -> Notifier:
    """憑證齊全時回傳 LineNotifier，否則回傳只寫 log 的 LoggingNotifier。"""
    if token and user_id:
        return LineNotifier(token, user_id)
    return LoggingNotifier()
