"""交易日判斷。

這裡的失敗模式特別昂貴：判成交易日就會在休市日對所有訂閱者發出買賣通知，
而休市日的「最新收盤」其實是前一個交易日，訊號完全失去意義。
"""

from __future__ import annotations

from datetime import date

import pytest
import requests

from stock_notify.trading_calendar import (
    TradingCalendarError,
    check_market_status,
    decide_market_status,
    fetch_holiday_calendar,
    parse_calendar,
    recent_trading_days,
)

HEADER = "date,name,isholiday,holidaycategory,description\n"


def csv_rows(*rows: str) -> str:
    return HEADER + "".join(rows)


def test_workday_is_trading_day() -> None:
    text = csv_rows("20260724,,否,,\n")
    assert decide_market_status(text, date(2026, 7, 24)) is None


def test_holiday_closes_market() -> None:
    text = csv_rows("20260101,中華民國開國紀念日,是,放假之紀念日及節日,\n")
    closure = decide_market_status(text, date(2026, 1, 1))
    assert closure is not None
    assert closure.name == "中華民國開國紀念日"
    assert closure.category == "放假之紀念日及節日"


def test_military_day_still_trades() -> None:
    """軍人節列在政府行事曆的假日中，但股市照常交易 —— 誤判會白白跳過一天。"""
    text = csv_rows("20260903,軍人節,是,放假之紀念日及節日,\n")
    assert decide_market_status(text, date(2026, 9, 3)) is None


def test_bom_prefixed_header_is_handled() -> None:
    """政府開放資料的 CSV 帶 UTF-8 BOM，第一個欄位名會變成 '﻿date'。

    若沒處理，所有日期都會比對失敗而每天都被當成交易日。
    """
    text = "﻿" + csv_rows("20260101,開國紀念日,是,放假之紀念日及節日,\n")
    closure = decide_market_status(text, date(2026, 1, 1))
    assert closure is not None
    assert closure.name == "開國紀念日"


def test_weekend_row_closes_market_even_without_name() -> None:
    text = csv_rows("20260725,,是,,\n")
    closure = decide_market_status(text, date(2026, 7, 25))
    assert closure is not None
    assert closure.name == "週末/假日"


def test_missing_date_falls_back_to_weekday_check() -> None:
    """行事曆沒有該日資料時（例如跨年度尚未更新），仍必須擋掉週末。"""
    text = csv_rows("20260101,開國紀念日,是,放假之紀念日及節日,\n")

    assert decide_market_status(text, date(2026, 7, 24)) is None, "週五 → 交易日"

    saturday = decide_market_status(text, date(2026, 7, 25))
    assert saturday is not None and saturday.category == "週末"

    sunday = decide_market_status(text, date(2026, 7, 26))
    assert sunday is not None and sunday.category == "週末"


class _FailingSession:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, url: str, timeout: int) -> object:
        self.calls += 1
        raise requests.ConnectionError("network down")


def test_api_failure_raises_instead_of_assuming_workday() -> None:
    """API 失敗必須中止，不能像舊版一樣預設「今日為工作日」而照常發通知。"""
    session = _FailingSession()

    with pytest.raises(TradingCalendarError):
        fetch_holiday_calendar(session, attempts=3)  # type: ignore[arg-type]

    assert session.calls == 3, "應重試到指定次數才放棄"


def test_check_market_status_propagates_fetch_failure() -> None:
    with pytest.raises(TradingCalendarError):
        check_market_status(_FailingSession(), date(2026, 7, 24))  # type: ignore[arg-type]


def test_recent_trading_days_skips_weekends_and_holidays() -> None:
    """回補歷史前先用行事曆算出真正的交易日，避免對休市日發出 6 秒一次的請求。"""
    text = csv_rows(
        "20260720,,否,,\n",  # 一
        "20260721,,否,,\n",  # 二
        "20260722,端午節,是,放假之紀念日及節日,\n",
        "20260723,,否,,\n",  # 四
        "20260724,,否,,\n",  # 五
        "20260725,,是,星期六、星期日,\n",
        "20260726,,是,星期六、星期日,\n",
    )
    records = parse_calendar(text)

    days = recent_trading_days(records, date(2026, 7, 26), count=3)
    assert days == [date(2026, 7, 21), date(2026, 7, 23), date(2026, 7, 24)]


def test_recent_trading_days_is_sorted_oldest_first() -> None:
    records = parse_calendar(csv_rows("20260722,端午節,是,放假之紀念日及節日,\n"))
    days = recent_trading_days(records, date(2026, 7, 24), count=5)
    assert days == sorted(days)
    assert date(2026, 7, 22) not in days, "假日不應納入"


def test_recent_trading_days_respects_lookback_bound() -> None:
    """行事曆異常時不應無止盡往回找。"""
    records = parse_calendar(csv_rows())
    days = recent_trading_days(records, date(2026, 7, 24), count=100, max_lookback=10)
    assert len(days) <= 10
