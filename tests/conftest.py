from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from stock_notify.models import PriceHistory

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def kdj_golden() -> dict[str, Any]:
    """重構前的 KDJ 實作對固定序列產生的數值，用來確保重構未改變演算法。"""
    return json.loads((FIXTURES / "kdj_golden.json").read_text())


@pytest.fixture(scope="session")
def crossover_series() -> dict[str, Any]:
    """經數值搜尋確認會產生 J 值穿越（或刻意不穿越）的收盤價序列。"""
    return json.loads((FIXTURES / "crossover_series.json").read_text())


def make_prices(closes: list[float], volumes: list[int] | None = None) -> PriceHistory:
    """由收盤價建構 PriceHistory，高低價各 ±1。"""
    n = len(closes)
    start = date(2026, 1, 1)
    return PriceHistory(
        dates=[start + timedelta(days=i) for i in range(n)],
        open=list(closes),
        high=[c + 1.0 for c in closes],
        low=[c - 1.0 for c in closes],
        close=list(closes),
        volume=volumes if volumes is not None else [1000] * n,
    )
