"""本益比判定。

門檻是嚴格不等式（< 20 買、> 40 賣），邊界值必須落在 HOLD。
證交所對無本益比的個股回傳空字串或 "-"，這些不能被當成 0 而誤判為買進。
"""

from __future__ import annotations

import pytest

from stock_notify.analysis.pe import analyze_pe, parse_pe
from stock_notify.models import Signal


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("19.99", Signal.BUY),
        ("0.01", Signal.BUY),
        ("20", Signal.HOLD),
        ("20.0", Signal.HOLD),
        ("30", Signal.HOLD),
        ("40", Signal.HOLD),
        ("40.01", Signal.SELL),
        ("1000", Signal.SELL),
    ],
)
def test_thresholds(raw: str, expected: Signal) -> None:
    assert analyze_pe(raw) is expected


@pytest.mark.parametrize("raw", ["", "-", None, "N/A", "abc"])
def test_missing_pe_is_invalid_not_zero(raw: str | None) -> None:
    """無效值必須是 INVALID。若被當成 0 會通過 PE < 20 的買進條件而發出錯誤訊號。"""
    assert analyze_pe(raw) is Signal.INVALID
    assert parse_pe(raw) is None


def test_accepts_numeric_input() -> None:
    assert analyze_pe(15.5) is Signal.BUY
    assert parse_pe(15.5) == 15.5
