"""櫃買中心（上櫃）回應解析。

與上市的解析有三處實際差異，每一處都會靜默地產生錯誤資料而非拋錯，
因此逐項驗證。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from stock_notify.sources.common import DataSourceError
from stock_notify.sources.tpex import parse_daily_quotes, parse_history, parse_pe

DAY = date(2026, 7, 24)

# 實際回應的欄位名帶前後空白，這是刻意保留的
HISTORY_FIELDS = [
    "代號",
    "名稱",
    "收盤 ",
    "漲跌",
    "開盤 ",
    "最高 ",
    "最低",
    "成交股數  ",
    " 成交金額(元)",
]


def history_payload(*rows: list[str], stat: str = "ok") -> dict[str, Any]:
    return {"stat": stat, "tables": [{"fields": HISTORY_FIELDS, "data": list(rows)}]}


def test_parse_pe_uses_tpex_field_names() -> None:
    result = parse_pe(
        [
            {
                "SecuritiesCompanyCode": "6488",
                "CompanyName": "環球晶",
                "PriceEarningRatio": "18.62",
            }
        ]
    )
    assert result["6488"].name == "環球晶"
    assert result["6488"].display == "18.62"


def test_parse_pe_drops_empty_ratios() -> None:
    """與上市一致：無本益比的個股不納入分析標的，否則會被當成 0 而誤判買進。"""
    result = parse_pe(
        [
            {"SecuritiesCompanyCode": "1111", "CompanyName": "無本益比", "PriceEarningRatio": ""},
            {
                "SecuritiesCompanyCode": "6488",
                "CompanyName": "環球晶",
                "PriceEarningRatio": "18.62",
            },
        ]
    )
    assert "1111" not in result
    assert "6488" in result


def test_parse_daily_quotes() -> None:
    quote_date, bars = parse_daily_quotes(
        [
            {
                "Date": "1150724",
                "SecuritiesCompanyCode": "6488",
                "CompanyName": "環球晶",
                "Open": "1070.00",
                "High": "1100.00",
                "Low": "1020.00",
                "Close": "1030.00",
                "TradingShares": "12256076",
            }
        ]
    )
    assert quote_date == DAY
    assert bars["6488"].open == 1070.0
    assert bars["6488"].high == 1100.0
    assert bars["6488"].low == 1020.0
    assert bars["6488"].close == 1030.0
    assert bars["6488"].volume == 12256076


def test_history_field_names_are_stripped_before_lookup() -> None:
    """上櫃的欄位名帶空白（'收盤 '、'成交股數  '）。

    不做 strip 就找不到欄位，函式會靜默回傳空 dict，該交易日被當成沒開盤。
    """
    bars = parse_history(
        history_payload(
            [
                "6488",
                "環球晶",
                "1,030.00",
                "-90.00",
                "1,070.00",
                "1,100.00",
                "1,020.00",
                "11,863,000",
                "12,431,255,000",
            ]
        ),
        DAY,
    )
    assert bars["6488"].close == 1030.0
    assert bars["6488"].open == 1070.0
    assert bars["6488"].volume == 11863000


def test_history_empty_on_non_trading_day() -> None:
    """上櫃在非交易日回傳 stat='ok' 但資料為空，無法用狀態碼區分。"""
    assert parse_history(history_payload(), DAY) == {}


def test_history_unknown_status_raises() -> None:
    """狀態非 ok 多半代表被限流或端點變更，不能當成沒開盤。"""
    with pytest.raises(DataSourceError):
        parse_history(history_payload(stat="error"), DAY)


def test_history_skips_rows_without_trades() -> None:
    bars = parse_history(
        history_payload(
            ["1111", "停牌股", "--", "0.00", "--", "--", "--", "0", "0"],
            [
                "6488",
                "環球晶",
                "1,030.00",
                "-90.00",
                "1,070.00",
                "1,100.00",
                "1,020.00",
                "100",
                "1",
            ],
        ),
        DAY,
    )
    assert "1111" not in bars
    assert "6488" in bars


def test_empty_daily_quotes_raises() -> None:
    with pytest.raises(DataSourceError):
        parse_daily_quotes([])
