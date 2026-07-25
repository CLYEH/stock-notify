"""本益比分析。"""

from __future__ import annotations

from stock_notify.config import PE_BUY_THRESHOLD, PE_SELL_THRESHOLD
from stock_notify.models import Signal


def parse_pe(raw: str | float | None) -> float | None:
    """將證交所回傳的本益比欄位轉為數值，無效值回傳 None。

    證交所對無本益比的股票會回傳空字串或 "-"。
    """
    if raw is None or raw == "" or raw == "-":
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def analyze_pe(raw: str | float | None) -> Signal:
    """低於買進門檻為 BUY、高於賣出門檻為 SELL，無法解析為 INVALID。"""
    pe_value = parse_pe(raw)
    if pe_value is None:
        return Signal.INVALID
    if pe_value < PE_BUY_THRESHOLD:
        return Signal.BUY
    if pe_value > PE_SELL_THRESHOLD:
        return Signal.SELL
    return Signal.HOLD
