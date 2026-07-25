"""證交所回應解析。

最危險的失敗模式是「被限流的錯誤回應被當成非交易日」——
程式會安靜地少抓幾天資料，KDJ 用殘缺序列算出來的訊號看起來完全正常。
"""

from __future__ import annotations

from datetime import date

import pytest

from stock_notify.sources.twse import (
    DataSourceError,
    parse_mi_index,
    parse_stock_day_all,
    roc_date_to_date,
)

DAY = date(2026, 7, 24)

MI_FIELDS = [
    "證券代號",
    "證券名稱",
    "成交股數",
    "成交筆數",
    "成交金額",
    "開盤價",
    "最高價",
    "最低價",
    "收盤價",
]


def mi_payload(*rows: list[str]) -> dict[str, object]:
    return {"stat": "OK", "tables": [{"fields": MI_FIELDS, "data": list(rows)}]}


def test_roc_date_conversion() -> None:
    assert roc_date_to_date("1150724") == date(2026, 7, 24)
    assert roc_date_to_date("1000101") == date(2011, 1, 1)


def test_parse_stock_day_all() -> None:
    quote_date, bars = parse_stock_day_all(
        [
            {
                "Date": "1150724",
                "Code": "2330",
                "Name": "台積電",
                "TradeVolume": "24,810,509",
                "OpeningPrice": "2,355.00",
                "HighestPrice": "2,365.00",
                "LowestPrice": "2,345.00",
                "ClosingPrice": "2,350.00",
            }
        ]
    )

    assert quote_date == date(2026, 7, 24)
    assert bars["2330"].close == 2350.0
    assert bars["2330"].high == 2365.0
    assert bars["2330"].volume == 24810509


def test_parse_mi_index_reads_by_field_name() -> None:
    """依欄位名稱定位而非固定索引 —— 證交所曾新增欄位，寫死索引會靜默錯位。"""
    bars = parse_mi_index(
        mi_payload(
            [
                "2330",
                "台積電",
                "24,810,509",
                "195,859",
                "58,407,263,735",
                "2,355.00",
                "2,365.00",
                "2,345.00",
                "2,350.00",
            ]
        ),
        DAY,
    )
    assert bars["2330"].open == 2355.0
    assert bars["2330"].close == 2350.0
    assert bars["2330"].date == DAY


def test_rows_without_trades_are_skipped() -> None:
    """無成交的個股價格欄位為 '--'，納入會讓 KDJ 算到 NaN。"""
    bars = parse_mi_index(
        mi_payload(
            ["00625K", "富邦上証+R", "0", "0", "0", "--", "--", "--", "--"],
            ["2330", "台積電", "100", "1", "1", "10.0", "11.0", "9.0", "10.5"],
        ),
        DAY,
    )
    assert "00625K" not in bars
    assert "2330" in bars


def test_known_no_data_status_means_non_trading_day() -> None:
    for stat in ("很抱歉，沒有符合條件的資料!", "查詢日期大於今日，請重新查詢!"):
        assert parse_mi_index({"stat": stat}, DAY) == {}


def test_unknown_status_raises_instead_of_looking_like_a_holiday() -> None:
    """被限流時證交所回的不是資料。若當成非交易日，會安靜產生殘缺的歷史序列。"""
    with pytest.raises(DataSourceError, match="頻率限制"):
        parse_mi_index({"stat": "請勿頻繁擷取"}, DAY)

    with pytest.raises(DataSourceError):
        parse_mi_index({}, DAY)


def test_empty_stock_day_all_raises() -> None:
    with pytest.raises(DataSourceError):
        parse_stock_day_all([])
