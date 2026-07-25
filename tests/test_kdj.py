"""KDJ 計算的回歸測試。

黃金值由重構前的實作產生。這些測試存在的理由是：KDJ 是整個系統唯一的
技術指標來源，數值一旦漂移，所有買賣訊號都會靜默地變成另一套規則，
而通知內容看起來仍然「正常」。因此重構後數值必須逐位相符。
"""

from __future__ import annotations

from typing import Any

import pytest

from stock_notify.analysis.kdj import InsufficientDataError, calculate_kdj
from stock_notify.config import KDJ_PERIOD


@pytest.mark.parametrize("length", ["9", "12", "30", "40"])
def test_matches_pre_refactor_values(kdj_golden: dict[str, Any], length: str) -> None:
    case = kdj_golden[length]
    result = calculate_kdj(case["high"], case["low"], case["close"])

    assert result.k == case["K"]
    assert result.d == case["D"]
    assert result.j == case["J"]

    for actual, expected in zip(result.j_series, case["J_series"], strict=True):
        assert actual == pytest.approx(expected, abs=1e-9)
    for actual, expected in zip(result.k_series, case["K_series"], strict=True):
        assert actual == pytest.approx(expected, abs=1e-9)


def test_series_length_matches_input(kdj_golden: dict[str, Any]) -> None:
    """J 序列必須與輸入等長 —— 訊號判定要取 [-2] 與 [-1]，長度錯位會讀到錯誤的日期。"""
    case = kdj_golden["40"]
    result = calculate_kdj(case["high"], case["low"], case["close"])
    assert len(result.j_series) == len(case["close"])
    assert len(result.k_series) == len(case["close"])
    assert len(result.d_series) == len(case["close"])


def test_vectorised_ewma_equals_recursive_definition(kdj_golden: dict[str, Any]) -> None:
    """向量化的 EWMA 必須等同 K[t] = 2/3·K[t-1] + 1/3·RSV[t] 的逐日遞迴定義。

    這是重構時最容易出錯的地方：pandas 的 ewm 預設沒有 seed，
    必須把初始值 50 預置於序列首才會與原始定義相符。
    """
    import pandas as pd

    case = kdj_golden["40"]
    high = pd.Series(case["high"], dtype="float64")
    low = pd.Series(case["low"], dtype="float64")
    close = pd.Series(case["close"], dtype="float64")

    rsv = (
        (close - low.rolling(KDJ_PERIOD).min())
        / (high.rolling(KDJ_PERIOD).max() - low.rolling(KDJ_PERIOD).min())
        * 100
    ).fillna(50)

    k_prev = d_prev = 50.0
    expected_j = []
    for r in rsv:
        k_prev = (2 / 3) * k_prev + (1 / 3) * r
        d_prev = (2 / 3) * d_prev + (1 / 3) * k_prev
        expected_j.append(3 * k_prev - 2 * d_prev)

    result = calculate_kdj(case["high"], case["low"], case["close"])
    for actual, expected in zip(result.j_series, expected_j, strict=True):
        assert actual == pytest.approx(expected, abs=1e-9)


def test_insufficient_data_raises() -> None:
    """資料不足必須拋錯而非回傳看似合理的數值，否則會被當成正常訊號使用。"""
    with pytest.raises(InsufficientDataError):
        calculate_kdj([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], [1.0, 2.0, 3.0])


def test_exactly_period_length_is_allowed(kdj_golden: dict[str, Any]) -> None:
    case = kdj_golden["9"]
    assert len(case["close"]) == KDJ_PERIOD
    assert calculate_kdj(case["high"], case["low"], case["close"]).j == case["J"]


def test_flat_prices_do_not_produce_nan() -> None:
    """完全持平的價格會讓 RSV 分母為 0；必須補成中性值而非 NaN 汙染整條序列。"""
    closes = [100.0] * 30
    result = calculate_kdj([101.0] * 30, [99.0] * 30, closes)
    assert all(v == v for v in result.j_series)  # NaN != NaN
