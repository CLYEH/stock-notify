"""跨層共用的資料模型。

所有欄位皆為明確定義的 dataclass 欄位，取代舊版到處傳遞、
欄位時有時無的 dict。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum


class Signal(str, Enum):
    """買賣訊號。"""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    INVALID = "invalid"


@dataclass(frozen=True)
class Bar:
    """單一股票在單一交易日的 OHLCV。"""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class PriceHistory:
    """單一股票的歷史 OHLCV 序列，六個 list 等長且依日期遞增。"""

    dates: list[date]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[int]

    def __len__(self) -> int:
        return len(self.close)

    @classmethod
    def from_bars(cls, bars: Sequence[Bar]) -> PriceHistory:
        """由依日期遞增排序的 Bar 序列建構。"""
        return cls(
            dates=[b.date for b in bars],
            open=[b.open for b in bars],
            high=[b.high for b in bars],
            low=[b.low for b in bars],
            close=[b.close for b in bars],
            volume=[b.volume for b in bars],
        )


@dataclass(frozen=True)
class KDJResult:
    """KDJ 計算結果。k/d/j 為最新一日（四捨五入至小數 2 位），series 為完整序列。"""

    k: float
    d: float
    j: float
    k_series: list[float]
    d_series: list[float]
    j_series: list[float]


@dataclass(frozen=True)
class StockAnalysis:
    """單一股票的分析結果。"""

    code: str
    name: str
    signal: Signal
    pe_ratio: float | None
    """解析後的本益比；None 代表 API 未提供或無法解析。"""
    pe_display: str | None
    """證交所回傳的原始字串。通知訊息沿用原始文字以維持既有輸出格式。"""
    j_value: float | None
    yesterday_j: float | None
    volume_spike: bool
    data_length: int
