"""證交所回應解析。

最危險的失敗模式是「被限流的錯誤回應被當成非交易日」——
程式會安靜地少抓幾天資料，KDJ 用殘缺序列算出來的訊號看起來完全正常。
"""

from __future__ import annotations

from datetime import date

import pytest
import requests

from stock_notify.sources.common import DataSourceError, roc_date_to_date
from stock_notify.sources.twse import parse_mi_index, parse_stock_day_all

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


# --------------------------------------------------------------------------
# 抓取重試
# --------------------------------------------------------------------------


class _FlakySession:
    """前 `failures` 次拋出網路錯誤，之後成功。"""

    def __init__(self, failures: int, payload: object = None) -> None:
        self.failures = failures
        self.payload = payload if payload is not None else {"ok": True}
        self.calls = 0

    def get(self, url: str, params: object = None, timeout: int = 0) -> object:
        self.calls += 1
        if self.calls <= self.failures:
            raise requests.ConnectionError("Response ended prematurely")
        return _Response(self.payload)


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def test_transient_network_error_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """交易所端點偶發連線中斷，不該讓整天的通知消失。

    2026-07-27 的手動執行就是這樣失敗的：上櫃行情端點回傳
    "Response ended prematurely"，當時沒有重試，整個流程 exit 1，
    連已經算好的上市 17 個訊號也一併丟失。
    """
    from stock_notify.sources.common import get_json

    monkeypatch.setattr("stock_notify.sources.common.time.sleep", lambda _: None)
    session = _FlakySession(failures=2, payload={"data": 1})

    result = get_json(session, "https://example.test", "測試抓取")  # type: ignore[arg-type]

    assert result == {"data": 1}
    assert session.calls == 3


def test_retry_gives_up_and_reports_the_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    from stock_notify.sources.common import get_json

    monkeypatch.setattr("stock_notify.sources.common.time.sleep", lambda _: None)
    session = _FlakySession(failures=99)

    with pytest.raises(DataSourceError, match="已重試 3 次"):
        get_json(session, "https://example.test", "測試抓取")  # type: ignore[arg-type]

    assert session.calls == 3


def test_retry_backoff_respects_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """重試間隔必須 >= 限流間隔，否則重試本身會觸發鎖 IP 一小時。"""
    from stock_notify.config import TWSE_REQUEST_INTERVAL_SECONDS
    from stock_notify.sources.common import get_json

    slept: list[float] = []
    monkeypatch.setattr("stock_notify.sources.common.time.sleep", slept.append)

    get_json(_FlakySession(failures=1), "https://example.test", "測試")  # type: ignore[arg-type]

    assert slept, "失敗後應等待再重試"
    assert all(s >= TWSE_REQUEST_INTERVAL_SECONDS for s in slept)
