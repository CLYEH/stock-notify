"""櫃買中心（上櫃）資料抓取。

結構與 `sources.twse` 對稱：本益比端點同時作為分析標的清單，
每日行情一個 request 涵蓋全市場，歷史則逐日回補。

與上市的三個差異：
  * 欄位名帶前後空白（例如 '收盤 '、'成交股數  '），必須 strip 後再定位。
  * 歷史端點在非交易日回傳 `stat: 'ok'` 但資料為空，無法用狀態碼區分，
    只能以筆數判斷。
  * 主機為 www.tpex.org.tw，限流額度與證交所獨立。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import requests

from stock_notify.config import (
    HTTP_TIMEOUT_SECONDS,
    TPEX_DAILY_QUOTES_URL,
    TPEX_HISTORY_URL,
    TPEX_PE_URL,
)
from stock_notify.models import Bar
from stock_notify.sources.common import DataSourceError, PeInfo, build_bar, roc_date_to_date

logger = logging.getLogger(__name__)

_HISTORY_FIELDS = {
    "code": "代號",
    "open": "開盤",
    "high": "最高",
    "low": "最低",
    "close": "收盤",
    "volume": "成交股數",
}


def parse_pe(payload: list[dict[str, str]]) -> dict[str, PeInfo]:
    """解析上櫃本益比。與上市一致，本益比為空的個股不納入分析標的。"""
    return {
        item["SecuritiesCompanyCode"]: PeInfo(
            name=item.get("CompanyName") or item["SecuritiesCompanyCode"],
            display=item["PriceEarningRatio"],
        )
        for item in payload
        if item.get("SecuritiesCompanyCode") and item.get("PriceEarningRatio")
    }


def parse_daily_quotes(payload: list[dict[str, str]]) -> tuple[date, dict[str, Bar]]:
    """解析上櫃每日收盤行情。

    Raises:
        DataSourceError: 回應為空。
    """
    if not payload:
        raise DataSourceError("上櫃每日行情回應為空")

    quote_date = roc_date_to_date(payload[0]["Date"])
    bars: dict[str, Bar] = {}

    for item in payload:
        bar = build_bar(
            quote_date,
            item.get("Open", ""),
            item.get("High", ""),
            item.get("Low", ""),
            item.get("Close", ""),
            item.get("TradingShares", ""),
        )
        if bar is not None:
            bars[item["SecuritiesCompanyCode"]] = bar

    return quote_date, bars


def parse_history(payload: dict[str, Any], quote_date: date) -> dict[str, Bar]:
    """解析上櫃歷史行情。非交易日回傳空 dict。

    Raises:
        DataSourceError: 回應狀態非預期（多半代表被限流或端點變更）。
    """
    stat = str(payload.get("stat", "")).lower()
    if stat != "ok":
        raise DataSourceError(
            f"上櫃歷史行情於 {quote_date.isoformat()} 回傳非預期狀態 {payload.get('stat')!r}"
        )

    for table in payload.get("tables", []):
        # 欄位名帶前後空白，例如 '收盤 '、'成交股數  '
        fields = [str(name).strip() for name in table.get("fields") or []]
        if not all(label in fields for label in _HISTORY_FIELDS.values()):
            continue

        idx = {key: fields.index(label) for key, label in _HISTORY_FIELDS.items()}
        bars: dict[str, Bar] = {}
        for row in table.get("data", []):
            bar = build_bar(
                quote_date,
                row[idx["open"]],
                row[idx["high"]],
                row[idx["low"]],
                row[idx["close"]],
                row[idx["volume"]],
            )
            if bar is not None:
                bars[row[idx["code"]].strip()] = bar
        return bars

    return {}


def fetch_pe_data(session: requests.Session) -> dict[str, PeInfo]:
    """取得上櫃全市場本益比，同時作為分析標的清單。"""
    logger.info("取得上櫃本益比資料")
    try:
        response = session.get(TPEX_PE_URL, timeout=HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise DataSourceError(f"取得上櫃本益比資料失敗: {exc}") from exc

    result = parse_pe(payload)
    logger.info("上櫃本益比資料 %d 檔（原始 %d 筆）", len(result), len(payload))
    return result


def fetch_latest_quotes(session: requests.Session) -> tuple[date, dict[str, Bar]]:
    """取得上櫃最新交易日的全市場 OHLCV（一個 request）。"""
    logger.info("取得上櫃最新交易日全市場行情")
    try:
        response = session.get(TPEX_DAILY_QUOTES_URL, timeout=HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise DataSourceError(f"取得上櫃最新行情失敗: {exc}") from exc

    quote_date, bars = parse_daily_quotes(payload)
    logger.info("上櫃最新行情日期 %s，共 %d 檔", quote_date.isoformat(), len(bars))
    return quote_date, bars


def fetch_quotes_for_date(session: requests.Session, day: date) -> dict[str, Bar]:
    """取得上櫃指定日期的全市場 OHLCV。非交易日回傳空 dict。"""
    try:
        response = session.get(
            TPEX_HISTORY_URL,
            params={"date": day.strftime("%Y/%m/%d"), "type": "EW", "response": "json"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise DataSourceError(f"取得上櫃 {day.isoformat()} 行情失敗: {exc}") from exc

    return parse_history(payload, day)
