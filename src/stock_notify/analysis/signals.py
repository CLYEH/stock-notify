"""買賣訊號判定 —— 本系統唯一的業務規則所在處。

規則（crossover，非單純的高低門檻）：
  買進：昨日 J > 10 且 今日 J < 10，且 PE < 20
  賣出：昨日 J < 90 且 今日 J > 90，且 PE > 40

用「穿越」而非「所在區間」是刻意的：J 值長期低於 10 的股票不會每天重複觸發，
只有由上往下跨過門檻的當天才視為訊號。

KDJ 資料不足 30 個交易日時一律 HOLD。舊版在此情況下會依 PE 單獨產生
weak_buy/weak_sell，但那些訊號從未進入通知，且與「J 與 PE 必須同時成立」
的規格牴觸，已移除。
"""

from __future__ import annotations

from collections.abc import Sequence

from stock_notify.analysis.kdj import calculate_kdj
from stock_notify.analysis.pe import analyze_pe, parse_pe
from stock_notify.config import (
    J_BUY_THRESHOLD,
    J_SELL_THRESHOLD,
    KDJ_PERIOD,
    KDJ_SUFFICIENT_LENGTH,
)
from stock_notify.models import PriceHistory, Signal, StockAnalysis


def check_volume_spike(volumes: Sequence[float], multiplier: float) -> bool:
    """最新成交量是否達前一日的 `multiplier` 倍以上。"""
    if len(volumes) < 2:
        return False
    previous = volumes[-2]
    if previous == 0:
        return False
    return volumes[-1] >= previous * multiplier


def j_crossover_signal(j_series: Sequence[float]) -> Signal:
    """依 J 值的跨日穿越判定訊號。少於兩日資料無法判斷穿越，回傳 INVALID。"""
    if len(j_series) < 2:
        return Signal.INVALID

    yesterday, today = j_series[-2], j_series[-1]
    if yesterday > J_BUY_THRESHOLD and today < J_BUY_THRESHOLD:
        return Signal.BUY
    if yesterday < J_SELL_THRESHOLD and today > J_SELL_THRESHOLD:
        return Signal.SELL
    return Signal.HOLD


def analyze_stock(
    code: str,
    name: str,
    pe_display: str | None,
    prices: PriceHistory,
    volume_multiplier: float,
) -> StockAnalysis:
    """結合 PE 與 KDJ 判定單一股票的訊號。純函式，不做 I/O。"""
    pe_signal = analyze_pe(pe_display)

    j_value: float | None = None
    yesterday_j: float | None = None
    kdj_signal = Signal.INVALID
    volume_spike = False
    data_length = len(prices)

    if data_length >= KDJ_PERIOD:
        kdj = calculate_kdj(prices.high, prices.low, prices.close)
        j_value = kdj.j
        kdj_signal = j_crossover_signal(kdj.j_series)
        if len(kdj.j_series) >= 2:
            yesterday_j = round(kdj.j_series[-2], 2)
        volume_spike = check_volume_spike(prices.volume, volume_multiplier)

    signal = Signal.HOLD
    if (
        data_length >= KDJ_SUFFICIENT_LENGTH
        and kdj_signal is pe_signal
        and kdj_signal in (Signal.BUY, Signal.SELL)
    ):
        signal = kdj_signal

    return StockAnalysis(
        code=code,
        name=name,
        signal=signal,
        pe_ratio=parse_pe(pe_display),
        pe_display=pe_display,
        j_value=j_value,
        yesterday_j=yesterday_j,
        volume_spike=volume_spike,
        data_length=data_length,
    )
