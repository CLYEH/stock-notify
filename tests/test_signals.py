"""買賣訊號規則。

這裡是整個系統唯一決定「今天要不要通知使用者買賣」的地方，
規則出錯會直接造成錯誤的投資訊號，因此測試涵蓋每一條分支。
"""

from __future__ import annotations

from typing import Any

import pytest

from conftest import make_prices
from stock_notify.analysis.signals import (
    analyze_stock,
    check_volume_spike,
    j_crossover_signal,
)
from stock_notify.config import KDJ_SUFFICIENT_LENGTH
from stock_notify.models import Signal

# --------------------------------------------------------------------------
# J 值穿越規則
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("yesterday", "today", "expected"),
    [
        # 由上往下穿越 10 → 買進
        (15.0, 5.0, Signal.BUY),
        (10.01, 9.99, Signal.BUY),
        # 由下往上穿越 90 → 賣出
        (85.0, 95.0, Signal.SELL),
        (89.99, 90.01, Signal.SELL),
        # 未穿越
        (50.0, 50.0, Signal.HOLD),
        (95.0, 85.0, Signal.HOLD),
        (5.0, 15.0, Signal.HOLD),
    ],
)
def test_crossover_direction(yesterday: float, today: float, expected: Signal) -> None:
    assert j_crossover_signal([yesterday, today]) is expected


@pytest.mark.parametrize(("yesterday", "today"), [(5.0, 3.0), (2.0, 8.0), (9.9, 9.8)])
def test_persistently_oversold_does_not_signal_buy(yesterday: float, today: float) -> None:
    """這是 crossover 與「J < 10 就買」的關鍵差異。

    長期趴在 10 以下的股票在 level 規則下會天天出現在通知裡，
    crossover 規則只在跌破當天觸發一次。
    """
    assert j_crossover_signal([yesterday, today]) is Signal.HOLD


@pytest.mark.parametrize(("yesterday", "today"), [(95.0, 97.0), (92.0, 99.0)])
def test_persistently_overbought_does_not_signal_sell(yesterday: float, today: float) -> None:
    assert j_crossover_signal([yesterday, today]) is Signal.HOLD


def test_boundary_values_are_not_crossings() -> None:
    """門檻是嚴格不等式：剛好等於 10 或 90 不算穿越。"""
    assert j_crossover_signal([15.0, 10.0]) is Signal.HOLD
    assert j_crossover_signal([10.0, 5.0]) is Signal.HOLD
    assert j_crossover_signal([85.0, 90.0]) is Signal.HOLD
    assert j_crossover_signal([90.0, 95.0]) is Signal.HOLD


def test_single_day_cannot_determine_crossing() -> None:
    assert j_crossover_signal([5.0]) is Signal.INVALID
    assert j_crossover_signal([]) is Signal.INVALID


# --------------------------------------------------------------------------
# 成交量放大
# --------------------------------------------------------------------------


def test_volume_spike_requires_multiplier() -> None:
    assert check_volume_spike([100, 200], 2.0) is True
    assert check_volume_spike([100, 199], 2.0) is False


def test_volume_spike_edge_cases() -> None:
    assert check_volume_spike([100], 2.0) is False, "只有一天無法比較"
    assert check_volume_spike([], 2.0) is False
    assert check_volume_spike([0, 500], 2.0) is False, "前一日為 0 時任何量都會是無限倍"


# --------------------------------------------------------------------------
# PE 與 KDJ 的組合
# --------------------------------------------------------------------------


def _analyze(closes: list[float], pe: str | None, **kw: Any) -> Any:
    return analyze_stock(
        code="2330",
        name="台積電",
        pe_display=pe,
        prices=make_prices(closes, **kw),
        volume_multiplier=2.0,
    )


def test_buy_requires_both_kdj_and_pe(crossover_series: dict[str, Any]) -> None:
    closes = crossover_series["buy_crossover"]["closes"]
    assert len(closes) >= KDJ_SUFFICIENT_LENGTH

    assert _analyze(closes, "15").signal is Signal.BUY, "J 穿越 + PE < 20 → 買進"
    assert _analyze(closes, "30").signal is Signal.HOLD, "PE 落在中間 → 不買"
    assert _analyze(closes, "50").signal is Signal.HOLD, "PE 高 → 不買"
    assert _analyze(closes, "").signal is Signal.HOLD, "無 PE 資料 → 不買"


def test_sell_requires_both_kdj_and_pe(crossover_series: dict[str, Any]) -> None:
    closes = crossover_series["sell_crossover"]["closes"]

    assert _analyze(closes, "50").signal is Signal.SELL, "J 穿越 + PE > 40 → 賣出"
    assert _analyze(closes, "30").signal is Signal.HOLD
    assert _analyze(closes, "15").signal is Signal.HOLD


def test_persistent_oversold_with_low_pe_still_holds(crossover_series: dict[str, Any]) -> None:
    """實際價格序列版本的 crossover 驗證：J 連兩日低於 10 但沒有穿越，即使 PE 很低也不買。"""
    case = crossover_series["persistent_oversold"]
    assert case["j_prev"] < 10 and case["j_last"] < 10

    result = _analyze(case["closes"], "5")
    assert result.j_value is not None and result.j_value < 10
    assert result.signal is Signal.HOLD


def test_insufficient_history_never_signals(crossover_series: dict[str, Any]) -> None:
    """資料少於 30 個交易日一律 HOLD。

    舊版在此情況下會只憑 PE 產生 weak_buy/weak_sell。那些訊號從未進入通知，
    且與「J 與 PE 必須同時成立」的規格牴觸，因此移除。此測試防止其復活。
    """
    closes = crossover_series["buy_crossover"]["closes"]
    short = closes[-(KDJ_SUFFICIENT_LENGTH - 1) :]
    assert len(short) < KDJ_SUFFICIENT_LENGTH

    result = _analyze(short, "5")
    assert result.signal is Signal.HOLD
    assert result.j_value is not None, "仍應計算 J 值供除錯，只是不產生訊號"


def test_too_short_for_kdj_produces_no_j_value() -> None:
    result = _analyze([100.0] * 5, "5")
    assert result.signal is Signal.HOLD
    assert result.j_value is None
    assert result.data_length == 5


def test_volume_spike_is_reported(crossover_series: dict[str, Any]) -> None:
    closes = crossover_series["buy_crossover"]["closes"]
    volumes = [1000] * (len(closes) - 1) + [5000]
    assert _analyze(closes, "15", volumes=volumes).volume_spike is True
    assert _analyze(closes, "15").volume_spike is False


def test_pe_display_preserves_api_text(crossover_series: dict[str, Any]) -> None:
    """通知訊息直接沿用證交所原始字串，避免 "12.30" 被格式化成 "12.3"。"""
    result = _analyze(crossover_series["buy_crossover"]["closes"], "12.30")
    assert result.pe_display == "12.30"
    assert result.pe_ratio == 12.3
