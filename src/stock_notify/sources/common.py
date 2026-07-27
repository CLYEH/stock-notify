"""上市與上櫃共用的抓取、解析工具與限流。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

from stock_notify.config import HTTP_TIMEOUT_SECONDS, TWSE_REQUEST_INTERVAL_SECONDS
from stock_notify.models import Bar

logger = logging.getLogger(__name__)

FETCH_ATTEMPTS = 3
"""單一端點的總嘗試次數。

交易所端點偶發連線中斷（實測 "Response ended prematurely"、
"RemoteDisconnected"）。這類抖動不該讓整天的通知消失。
"""


class DataSourceError(Exception):
    """無法從證交所／櫃買中心取得資料。"""


@dataclass(frozen=True)
class PeInfo:
    """個股本益比。`display` 保留 API 原始字串供通知訊息使用。"""

    name: str
    display: str


def get_json(
    session: requests.Session,
    url: str,
    description: str,
    params: dict[str, str] | None = None,
    attempts: int = FETCH_ATTEMPTS,
) -> Any:
    """發出 GET 並解析 JSON，暫時性失敗時重試。

    重試間隔採用限流間隔而非更短的退避 —— 受限流的端點若重試太快，
    反而會觸發鎖 IP 一小時，讓情況更糟。

    Raises:
        DataSourceError: 所有嘗試皆失敗。
    """
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            logger.warning("%s失敗 (第 %d/%d 次): %s", description, attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(TWSE_REQUEST_INTERVAL_SECONDS)

    raise DataSourceError(
        f"{description}失敗（已重試 {attempts} 次）: {last_error}"
    ) from last_error


def to_float(value: str) -> float | None:
    """解析帶千分位的數字。無成交的個股欄位為 '--'。"""
    cleaned = value.replace(",", "").strip()
    if not cleaned or cleaned == "--":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def roc_date_to_date(value: str) -> date:
    """將民國日期字串（如 '1150724'）轉為西元 date。"""
    text = value.strip()
    if len(text) != 7:
        raise ValueError(f"無法解析民國日期: {value!r}")
    return date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:7]))


def build_bar(
    quote_date: date, open_: str, high: str, low: str, close: str, volume: str
) -> Bar | None:
    """任一價格欄位缺值即回傳 None —— 停牌或無成交的個股不納入 KDJ 計算。"""
    values = [to_float(open_), to_float(high), to_float(low), to_float(close)]
    if any(v is None for v in values):
        return None
    open_value, high_value, low_value, close_value = values
    assert open_value is not None and high_value is not None
    assert low_value is not None and close_value is not None
    return Bar(
        date=quote_date,
        open=open_value,
        high=high_value,
        low=low_value,
        close=close_value,
        volume=int(to_float(volume) or 0.0),
    )


class Throttle:
    """確保對同一主機的連續請求間隔不小於指定秒數。

    上市與上櫃分屬 www.twse.com.tw 與 www.tpex.org.tw，額度各自獨立，
    因此每個市場使用各自的 Throttle 實例。
    """

    def __init__(self, interval: float = TWSE_REQUEST_INTERVAL_SECONDS) -> None:
        self._interval = interval
        self._last_call: float | None = None

    def wait(self) -> None:
        if self._last_call is not None:
            remaining = self._interval - (time.monotonic() - self._last_call)
            if remaining > 0:
                logger.debug("等待 %.1f 秒以符合頻率限制", remaining)
                time.sleep(remaining)
        self._last_call = time.monotonic()
