"""交易日判斷。

舊版有三套互相重疊的答案：假日 API、`weekday() < 5`、以及 cron 的 `1-5`。
本模組把前兩者合併為單一判斷，cron 僅作為排程而非正確性依據。

失敗策略為 fail-closed：假日 API 重試後仍無法取得資料時拋出
`TradingCalendarError`，由 CLI 中止執行。舊版在此情況下預設「今日為工作日」，
等於在休市日照常分析並發出買賣通知。

行事曆同時用於推算過去的交易日，讓歷史回補只對真正有開盤的日期發出請求。
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from io import StringIO

import requests

from stock_notify.config import (
    BACKFILL_MAX_CALENDAR_DAYS,
    HOLIDAY_API_ATTEMPTS,
    HOLIDAY_API_TIMEOUT_SECONDS,
    HOLIDAY_API_URL,
)

logger = logging.getLogger(__name__)

# 軍人節列在政府行事曆的假日中，但股市照常交易。
TRADING_DESPITE_HOLIDAY = frozenset({"軍人節"})


class TradingCalendarError(Exception):
    """無法確認今日是否為交易日。"""


@dataclass(frozen=True)
class MarketClosure:
    """休市原因。"""

    name: str
    category: str


@dataclass(frozen=True)
class HolidayRecord:
    """政府行事曆中對應某一天的原始紀錄。"""

    is_holiday: bool
    name: str
    category: str


def parse_calendar(csv_text: str) -> dict[date, HolidayRecord]:
    """將行事曆 CSV 解析為以日期為鍵的對照表。"""
    records: dict[date, HolidayRecord] = {}

    for row in csv.DictReader(StringIO(csv_text)):
        # 檔案帶 BOM 時第一個欄位名會是 '﻿date'
        raw_date = row.get("date") or row.get("﻿date")
        if not raw_date:
            continue
        try:
            day = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8]))
        except (ValueError, IndexError):
            continue
        records[day] = HolidayRecord(
            is_holiday=row.get("isholiday") == "是",
            name=(row.get("name") or "").strip(),
            category=(row.get("holidaycategory") or "").strip(),
        )

    return records


def find_holiday_record(csv_text: str, today: date) -> HolidayRecord | None:
    """從行事曆 CSV 取出今日紀錄，找不到時回傳 None。"""
    return parse_calendar(csv_text).get(today)


def closure_for(records: dict[date, HolidayRecord], day: date) -> MarketClosure | None:
    """判定某日是否休市。回傳 None 代表照常交易。純函式。"""
    record = records.get(day)

    if record is None:
        # 行事曆沒有這一天（通常是跨年度資料尚未更新），退回週末判斷
        if day.weekday() >= 5:
            return MarketClosure(name="週末", category="週末")
        return None

    if record.name in TRADING_DESPITE_HOLIDAY:
        return None

    if record.is_holiday:
        return MarketClosure(name=record.name or "週末/假日", category=record.category)

    return None


def decide_market_status(csv_text: str, today: date) -> MarketClosure | None:
    """判定今日是否休市。回傳 None 代表照常交易。純函式。"""
    closure = closure_for(parse_calendar(csv_text), today)
    if closure is None:
        record = find_holiday_record(csv_text, today)
        if record is not None and record.name in TRADING_DESPITE_HOLIDAY:
            logger.info("今日為%s，但股市照常交易", record.name)
    return closure


def recent_trading_days(
    records: dict[date, HolidayRecord],
    end: date,
    count: int,
    max_lookback: int = BACKFILL_MAX_CALENDAR_DAYS,
) -> list[date]:
    """回傳截至 `end`（含）為止最近 `count` 個交易日，由舊到新排序。

    僅依行事曆推算，不發任何請求 —— 目的正是避免對週末與假日做無謂的
    MI_INDEX 查詢（每個請求都要等 6 秒）。
    """
    days: list[date] = []
    cursor = end

    for _ in range(max_lookback):
        if len(days) == count:
            break
        if closure_for(records, cursor) is None:
            days.append(cursor)
        cursor -= timedelta(days=1)

    return sorted(days)


def fetch_holiday_calendar(session: requests.Session, attempts: int = HOLIDAY_API_ATTEMPTS) -> str:
    """下載行事曆 CSV，失敗時重試。

    Raises:
        TradingCalendarError: 所有嘗試皆失敗。
    """
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = session.get(HOLIDAY_API_URL, timeout=HOLIDAY_API_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("取得行事曆失敗 (第 %d/%d 次): %s", attempt, attempts, exc)

    raise TradingCalendarError(
        f"連續 {attempts} 次無法取得行事曆，無法確認今日是否為交易日"
    ) from last_error


def check_market_status(session: requests.Session, today: date) -> MarketClosure | None:
    """取得行事曆並判定今日是否休市。回傳 None 代表照常交易。"""
    logger.info("檢查 %s 是否為交易日", today.isoformat())
    closure = decide_market_status(fetch_holiday_calendar(session), today)
    if closure is None:
        logger.info("今日為交易日")
    else:
        logger.info("今日休市：%s (%s)", closure.name, closure.category)
    return closure
