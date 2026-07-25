"""上市與上櫃共用的解析工具與限流。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date

from stock_notify.config import TWSE_REQUEST_INTERVAL_SECONDS
from stock_notify.models import Bar

logger = logging.getLogger(__name__)


class DataSourceError(Exception):
    """無法從證交所／櫃買中心取得資料。"""


@dataclass(frozen=True)
class PeInfo:
    """個股本益比。`display` 保留 API 原始字串供通知訊息使用。"""

    name: str
    display: str


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
