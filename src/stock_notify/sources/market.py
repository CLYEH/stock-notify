"""市場定義。

上市與上櫃的資料端點形狀不同，但對 pipeline 而言介面一致：
取得本益比（同時是分析標的清單）、取得最新行情、取得指定日期的歷史行情。
新增市場只需在此註冊，pipeline 不需改動。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import requests

from stock_notify.models import Bar
from stock_notify.sources import tpex, twse
from stock_notify.sources.common import PeInfo


@dataclass(frozen=True)
class Market:
    name: str
    """顯示用名稱，例如「上市」。"""
    suffix: str
    """資料庫 symbol 的後綴，上市為 .TW、上櫃為 .TWO。"""
    fetch_pe: Callable[[requests.Session], dict[str, PeInfo]]
    fetch_latest_quotes: Callable[[requests.Session], tuple[date, dict[str, Bar]]]
    fetch_quotes_for_date: Callable[[requests.Session, date], dict[str, Bar]]

    def symbol(self, code: str) -> str:
        return f"{code}{self.suffix}"


MARKETS: tuple[Market, ...] = (
    Market(
        name="上市",
        suffix=".TW",
        fetch_pe=twse.fetch_pe_data,
        fetch_latest_quotes=twse.fetch_latest_quotes,
        fetch_quotes_for_date=twse.fetch_quotes_for_date,
    ),
    Market(
        name="上櫃",
        suffix=".TWO",
        fetch_pe=tpex.fetch_pe_data,
        fetch_latest_quotes=tpex.fetch_latest_quotes,
        fetch_quotes_for_date=tpex.fetch_quotes_for_date,
    ),
)
