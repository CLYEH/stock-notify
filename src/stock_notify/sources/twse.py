"""證交所（上市）資料抓取。

存取策略是本次重構最大的效能改變。舊版用 twstock 逐檔抓取，
每檔 3 個 request、約 1000 檔 = 約 3000 個 request；而證交所對
`www.twse.com.tw` 有約 6 秒一次的頻率限制，超過會鎖 IP 一小時。

改用全市場端點後：
  * 每日更新：`STOCK_DAY_ALL` 一個 request 取得全市場最新交易日 OHLCV。
  * 歷史回補：`MI_INDEX?date=` 一個 request 取得指定日期的全市場 OHLCV，
    僅在資料庫缺資料時使用，並強制 6 秒間隔。

`openapi.twse.com.tw`（STOCK_DAY_ALL / BWIBBU_ALL）與 `www.twse.com.tw`
（MI_INDEX）是不同服務，限流只套用在後者。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import requests

from stock_notify.config import (
    HTTP_TIMEOUT_SECONDS,
    MI_INDEX_URL,
    PE_DATA_URL,
    STOCK_DAY_ALL_URL,
)
from stock_notify.models import Bar
from stock_notify.sources.common import DataSourceError, PeInfo, build_bar, roc_date_to_date

logger = logging.getLogger(__name__)

NO_DATA_STATUSES = frozenset(
    {
        "很抱歉，沒有符合條件的資料!",
        "查詢日期大於今日，請重新查詢!",
    }
)
"""MI_INDEX 中代表「該日無行情」的已知狀態訊息。其餘狀態視為錯誤。"""


def parse_stock_day_all(payload: list[dict[str, str]]) -> tuple[date, dict[str, Bar]]:
    """解析 STOCK_DAY_ALL 回應。

    Raises:
        DataSourceError: 回應為空或欄位無法解析。
    """
    if not payload:
        raise DataSourceError("STOCK_DAY_ALL 回應為空")

    quote_date = roc_date_to_date(payload[0]["Date"])
    bars: dict[str, Bar] = {}

    for item in payload:
        bar = build_bar(
            quote_date,
            item.get("OpeningPrice", ""),
            item.get("HighestPrice", ""),
            item.get("LowestPrice", ""),
            item.get("ClosingPrice", ""),
            item.get("TradeVolume", ""),
        )
        if bar is not None:
            bars[item["Code"]] = bar

    return quote_date, bars


def parse_mi_index(payload: dict[str, Any], quote_date: date) -> dict[str, Bar]:
    """解析 MI_INDEX 回應中的每日收盤行情表。

    非交易日（或當日資料尚未公布）回傳空 dict —— 兩者的 `stat` 訊息相同，
    因此本函式不區分，由呼叫端依行事曆判斷。

    只有明確已知的「查無資料」訊息才視為非交易日。這點很重要：證交所限流
    時回的是錯誤內容而非資料，若一律當成非交易日，程式會安靜地產生殘缺的
    歷史序列，而輸出看起來完全正常。未知的回應一律拋錯。

    Raises:
        DataSourceError: 回應狀態非預期（多半代表被限流或端點變更）。
    """
    stat = payload.get("stat")
    if stat != "OK":
        if stat in NO_DATA_STATUSES:
            return {}
        raise DataSourceError(
            f"MI_INDEX 於 {quote_date.isoformat()} 回傳非預期狀態 {stat!r}；"
            "常見原因是超過證交所頻率限制（約 6 秒一次，超過會鎖 IP 一小時）"
        )

    for table in payload.get("tables", []):
        fields = table.get("fields") or []
        if "證券代號" not in fields or "收盤價" not in fields:
            continue

        idx = {name: i for i, name in enumerate(fields)}
        bars: dict[str, Bar] = {}
        for row in table.get("data", []):
            bar = build_bar(
                quote_date,
                row[idx["開盤價"]],
                row[idx["最高價"]],
                row[idx["最低價"]],
                row[idx["收盤價"]],
                row[idx["成交股數"]],
            )
            if bar is not None:
                bars[row[idx["證券代號"]].strip()] = bar
        return bars

    return {}


def fetch_pe_data(session: requests.Session) -> dict[str, PeInfo]:
    """取得上市全市場本益比。

    BWIBBU_ALL 僅涵蓋上市普通股（實測 1080 檔、無 ETF 與權證），
    因此同時作為分析標的清單，不需要額外的股票清單來源。
    """
    logger.info("取得上市本益比資料")
    try:
        response = session.get(PE_DATA_URL, timeout=HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise DataSourceError(f"取得本益比資料失敗: {exc}") from exc

    result = {
        item["Code"]: PeInfo(
            name=item.get("Name") or item["Code"],
            display=item["PEratio"],
        )
        for item in payload
        # 舊版同樣過濾掉本益比為空字串的個股，維持分析標的一致
        if item.get("Code") and item.get("PEratio")
    }
    logger.info("上市本益比資料 %d 檔（原始 %d 筆）", len(result), len(payload))
    return result


def fetch_latest_quotes(session: requests.Session) -> tuple[date, dict[str, Bar]]:
    """取得上市最新交易日的全市場 OHLCV（一個 request）。"""
    logger.info("取得上市最新交易日全市場行情")
    try:
        response = session.get(STOCK_DAY_ALL_URL, timeout=HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise DataSourceError(f"取得最新行情失敗: {exc}") from exc

    quote_date, bars = parse_stock_day_all(payload)
    logger.info("上市最新行情日期 %s，共 %d 檔", quote_date.isoformat(), len(bars))
    return quote_date, bars


def fetch_quotes_for_date(session: requests.Session, day: date) -> dict[str, Bar]:
    """取得上市指定日期的全市場 OHLCV。非交易日回傳空 dict。

    此端點位於 www.twse.com.tw，有約 6 秒一次的頻率限制，
    超過會鎖 IP 一小時，因此呼叫端必須透過 `Throttle` 控制間隔。
    """
    try:
        response = session.get(
            MI_INDEX_URL,
            params={"date": day.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise DataSourceError(f"取得 {day.isoformat()} 行情失敗: {exc}") from exc

    return parse_mi_index(payload, day)
