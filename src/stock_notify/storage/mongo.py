"""歷史價格儲存。

文件結構與舊版完全相同，既有的 MongoDB 資料無須遷移。

資料庫的用途是避免每天重複回補 30 個交易日 —— 證交所的歷史端點有 6 秒
頻率限制，冷啟動要 3 分鐘，有快取則每天只需 1 個請求。沒有 MongoDB 時
系統仍可運作（`NullPriceRepository`），只是每次執行都要付冷啟動成本。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Protocol

from pymongo import MongoClient, UpdateOne
from pymongo.server_api import ServerApi

from stock_notify.config import MONGO_COLLECTION_NAME, MONGO_DB_NAME
from stock_notify.models import PriceHistory

logger = logging.getLogger(__name__)


class PriceRepository(Protocol):
    """歷史價格的存取介面。"""

    def load_all(self) -> dict[str, PriceHistory]:
        """一次載入所有個股的歷史，以股票代碼為鍵。"""
        ...

    def stage(self, code: str, history: PriceHistory) -> None:
        """累積一筆待寫入的更新。"""
        ...

    def flush(self) -> None:
        """將累積的更新批次寫回。"""
        ...

    def close(self) -> None: ...


class NullPriceRepository:
    """無資料庫時使用。所有操作皆為 no-op，呼叫端不需要判斷 DB 是否存在。"""

    def load_all(self) -> dict[str, PriceHistory]:
        return {}

    def stage(self, code: str, history: PriceHistory) -> None:
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


def _to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _document_to_history(doc: dict[str, Any]) -> PriceHistory | None:
    """將 MongoDB 文件轉為 PriceHistory，無法安全使用的文件回傳 None。

    整筆丟棄而非局部修補是刻意的：六個欄位必須逐日對齊，補值或刪列都會讓
    KDJ 讀到錯位的資料而算出看似正常的錯誤訊號。丟棄的文件會被重新抓取。

    舊版由 twstock 寫入的資料中，停牌個股的價格會是 None（twstock 對 '--'
    的處理），因此必須容許這種文件存在並排除。
    """
    raw = doc.get("price_history") or {}
    dates = [_to_date(d) for d in raw.get("dates", [])]
    if not dates or any(d is None for d in dates):
        return None

    columns = [raw.get(name, []) for name in ("open", "high", "low", "close", "volume")]
    if any(len(col) != len(dates) for col in columns):
        return None
    if any(v is None for col in columns for v in col):
        return None

    open_, high, low, close, volume = columns
    try:
        return PriceHistory(
            dates=[d for d in dates if d is not None],
            open=[float(v) for v in open_],
            high=[float(v) for v in high],
            low=[float(v) for v in low],
            close=[float(v) for v in close],
            volume=[int(v) for v in volume],
        )
    except (TypeError, ValueError):
        return None


def _history_to_document(code: str, history: PriceHistory) -> dict[str, Any]:
    """維持舊版的文件結構，既有資料無須遷移。"""
    # BSON 不支援 date，只支援 datetime
    stored_dates = [datetime(d.year, d.month, d.day) for d in history.dates]
    return {
        "symbol": f"{code}.TW",
        "code": code,
        "name": "",
        "date": history.dates[-1].strftime("%Y-%m-%d"),
        "latest_data": {
            "date": stored_dates[-1],
            "open": history.open[-1],
            "high": history.high[-1],
            "low": history.low[-1],
            "close": history.close[-1],
            "volume": history.volume[-1],
        },
        "price_history": {
            "dates": stored_dates,
            "open": history.open,
            "high": history.high,
            "low": history.low,
            "close": history.close,
            "volume": history.volume,
        },
        "data_length": len(history),
        "updated_at": datetime.now(),
        "has_sufficient_data": len(history) >= 30,
    }


class MongoPriceRepository:
    """以 MongoDB 儲存歷史價格。"""

    def __init__(self, uri: str) -> None:
        self._client: MongoClient[dict[str, Any]] = MongoClient(uri, server_api=ServerApi("1"))
        self._client.admin.command("ping")
        self._collection = self._client[MONGO_DB_NAME][MONGO_COLLECTION_NAME]
        self._pending: list[UpdateOne] = []
        logger.info("MongoDB 連線成功")

    def load_all(self) -> dict[str, PriceHistory]:
        histories: dict[str, PriceHistory] = {}
        skipped = 0

        # 依 updated_at 降冪，遇到同一 symbol 的重複文件時保留最新的一筆
        for doc in self._collection.find({}).sort("updated_at", -1):
            code = doc.get("code")
            if not code or code in histories:
                continue
            history = _document_to_history(doc)
            if history is None:
                skipped += 1
                continue
            histories[code] = history

        logger.info("自 MongoDB 載入 %d 檔歷史資料（略過 %d 筆無效）", len(histories), skipped)
        return histories

    def stage(self, code: str, history: PriceHistory) -> None:
        if len(history) == 0:
            return
        self._pending.append(
            UpdateOne(
                {"symbol": f"{code}.TW"},
                {"$set": _history_to_document(code, history)},
                upsert=True,
            )
        )

    def flush(self) -> None:
        if not self._pending:
            return
        try:
            result = self._collection.bulk_write(self._pending, ordered=False)
            logger.info(
                "寫入 MongoDB：新增 %d 筆 / 更新 %d 筆",
                result.upserted_count,
                result.modified_count,
            )
        finally:
            self._pending = []

    def close(self) -> None:
        self._client.close()


def create_repository(mongo_uri: str | None) -> PriceRepository:
    """建立儲存層。連線失敗時降級為 NullPriceRepository 並發出警告。"""
    if not mongo_uri:
        logger.warning("未設定 MONGO_URI，本次執行不使用歷史快取")
        return NullPriceRepository()

    try:
        return MongoPriceRepository(mongo_uri)
    except Exception as exc:
        logger.warning("MongoDB 連線失敗，改為不使用歷史快取：%s", exc)
        return NullPriceRepository()
