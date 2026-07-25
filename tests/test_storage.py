"""歷史價格儲存。

文件結構必須與舊版一致，否則既有 MongoDB 資料在升級後全部讀不出來，
系統會在毫無錯誤訊息的情況下對全市場做一次冷啟動回補。
"""

from __future__ import annotations

from datetime import date, datetime

from conftest import make_prices
from stock_notify.models import PriceHistory
from stock_notify.storage.mongo import (
    NullPriceRepository,
    _document_to_history,
    _history_to_document,
    create_repository,
)


def test_document_shape_matches_legacy_schema() -> None:
    history = make_prices([10.0, 11.0, 12.0])
    doc = _history_to_document("2330.TW", history)

    assert doc["symbol"] == "2330.TW"
    assert doc["code"] == "2330"
    assert doc["data_length"] == 3
    assert doc["has_sufficient_data"] is False
    assert set(doc["price_history"]) == {"dates", "open", "high", "low", "close", "volume"}
    assert set(doc["latest_data"]) == {"date", "open", "high", "low", "close", "volume"}
    assert doc["latest_data"]["close"] == 12.0


def test_dates_are_stored_as_datetime() -> None:
    """BSON 不支援 date，只支援 datetime；存成 date 會在寫入時才爆炸。"""
    doc = _history_to_document("2330.TW", make_prices([10.0]))
    assert all(isinstance(d, datetime) for d in doc["price_history"]["dates"])
    assert isinstance(doc["latest_data"]["date"], datetime)


def test_round_trip_preserves_history() -> None:
    original = make_prices([10.0, 11.0, 12.0], volumes=[100, 200, 300])
    restored = _document_to_history(_history_to_document("2330.TW", original))

    assert restored is not None
    assert restored.close == original.close
    assert restored.volume == original.volume
    assert restored.dates == original.dates


def test_reads_legacy_documents_with_datetime_dates() -> None:
    """舊版由 twstock 產生的資料，日期是 datetime。"""
    doc = {
        "code": "2330",
        "price_history": {
            "dates": [datetime(2026, 7, 23), datetime(2026, 7, 24)],
            "open": [10.0, 11.0],
            "high": [11.0, 12.0],
            "low": [9.0, 10.0],
            "close": [10.5, 11.5],
            "volume": [100, 200],
        },
    }
    history = _document_to_history(doc)
    assert history is not None
    assert history.dates == [date(2026, 7, 23), date(2026, 7, 24)]


def test_inconsistent_document_is_rejected() -> None:
    """欄位長度不一致的文件會讓 KDJ 讀到錯位的資料，寧可整筆丟棄重抓。"""
    doc = {
        "code": "2330",
        "price_history": {
            "dates": [datetime(2026, 7, 23), datetime(2026, 7, 24)],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [100],
        },
    }
    assert _document_to_history(doc) is None


def test_empty_document_is_rejected() -> None:
    assert _document_to_history({"code": "2330"}) is None


def test_null_repository_is_a_working_no_op() -> None:
    """未設定 MONGO_URI 時整套流程仍要能跑完，呼叫端不需要判斷 DB 是否存在。"""
    repo = NullPriceRepository()
    assert repo.load_all() == {}
    repo.stage("2330.TW", make_prices([10.0]))
    repo.flush()
    repo.close()


def test_create_repository_without_uri_degrades_gracefully() -> None:
    assert isinstance(create_repository(None), NullPriceRepository)
    assert isinstance(create_repository(""), NullPriceRepository)


def test_empty_history_is_not_staged() -> None:
    repo = NullPriceRepository()
    repo.stage("2330.TW", PriceHistory([], [], [], [], [], []))


def test_legacy_document_with_none_prices_is_rejected() -> None:
    """舊版 twstock 對停牌個股的 '--' 會寫成 None。

    這是實機執行時真的踩到的情況。若不排除，float(None) 會讓整個流程崩潰；
    若補 0 或刪列，六個欄位會失去逐日對齊，KDJ 會算出看似正常的錯誤訊號。
    """
    doc = {
        "code": "2330",
        "price_history": {
            "dates": [datetime(2026, 7, 23), datetime(2026, 7, 24)],
            "open": [None, 11.0],
            "high": [None, 12.0],
            "low": [None, 10.0],
            "close": [None, 11.5],
            "volume": [0, 200],
        },
    }
    assert _document_to_history(doc) is None


def test_non_numeric_values_are_rejected() -> None:
    doc = {
        "code": "2330",
        "price_history": {
            "dates": [datetime(2026, 7, 24)],
            "open": ["--"],
            "high": ["--"],
            "low": ["--"],
            "close": ["--"],
            "volume": [0],
        },
    }
    assert _document_to_history(doc) is None
