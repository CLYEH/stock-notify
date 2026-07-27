"""行情組裝邏輯。

這一層決定要對交易所發出幾個請求。每個歷史請求都要等 6 秒，
多抓是浪費、少抓則會用殘缺的序列算 KDJ，兩種錯誤都很貴。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from stock_notify.config import DEFAULT_DAYS, TRADING_DAY_BUFFER
from stock_notify.models import Bar, PriceHistory
from stock_notify.pipeline import _collect_quotes
from stock_notify.sources.market import Market

DAYS = [date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22)]
MARKET_DATE = DAYS[-1]
UNIVERSE = {"A", "B", "C"}


def bars_for(codes: list[str], day: date) -> dict[str, Bar]:
    return {c: Bar(date=day, open=1.0, high=2.0, low=0.5, close=1.5, volume=10) for c in codes}


def history_for(days: list[date]) -> PriceHistory:
    return PriceHistory(
        dates=list(days),
        open=[10.0] * len(days),
        high=[11.0] * len(days),
        low=[9.0] * len(days),
        close=[10.5] * len(days),
        volume=[100] * len(days),
    )


class NoThrottle:
    def wait(self) -> None:
        return None


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """限流在別的測試驗證，這裡只關心請求了哪些日期。"""
    monkeypatch.setattr("stock_notify.pipeline.Throttle", NoThrottle)


def make_market(
    requested: list[date],
    responses: dict[date, dict[str, Bar]] | None = None,
    suffix: str = ".TW",
) -> Market:
    def fetch_for_date(session: object, day: date) -> dict[str, Bar]:
        requested.append(day)
        if responses is not None and day in responses:
            return responses[day]
        return bars_for(sorted(UNIVERSE), day)

    def unused(*args: object, **kwargs: object) -> object:
        raise AssertionError("不應被呼叫")

    return Market(
        name="測試市場",
        suffix=suffix,
        fetch_pe=unused,  # type: ignore[arg-type]
        fetch_latest_quotes=unused,  # type: ignore[arg-type]
        fetch_quotes_for_date=fetch_for_date,
    )


def collect(
    market: Market,
    cached: dict[str, PriceHistory],
    trading_days: list[date] | None = None,
    market_date: date | None = None,
) -> dict[date, dict[str, Bar]]:
    days = trading_days or DAYS
    latest = market_date or days[-1]
    return _collect_quotes(
        session=None,  # type: ignore[arg-type]
        market=market,
        cached=cached,
        trading_days=days,
        market_date=latest,
        latest_bars=bars_for(sorted(UNIVERSE), latest),
        universe=set(UNIVERSE),
    )


def test_fully_cached_history_needs_no_requests() -> None:
    """穩態下每天只需要最新一日的行情，歷史全部來自資料庫。"""
    requested: list[date] = []
    cached = {f"{c}.TW": history_for(DAYS) for c in UNIVERSE}

    quotes = collect(make_market(requested), cached)

    assert requested == []
    assert set(quotes) == set(DAYS)


def test_empty_cache_backfills_every_day_except_the_latest() -> None:
    """冷啟動時最新一日已由每日行情端點取得，不重複請求。"""
    requested: list[date] = []
    collect(make_market(requested), {})

    assert requested == DAYS[:-1]
    assert MARKET_DATE not in requested


def test_thinly_covered_day_is_refetched() -> None:
    """快取只涵蓋少數個股的日期要重抓。

    否則新上市個股永遠湊不滿 30 天，每天都被判為資料不足而無法產生訊號。
    """
    requested: list[date] = []
    collect(make_market(requested), {"A.TW": history_for(DAYS)})

    assert DAYS[0] in requested
    assert DAYS[1] in requested


def test_other_market_cache_is_ignored() -> None:
    """上櫃的快取不能被當成上市的資料，否則會混入錯誤市場的價格。"""
    requested: list[date] = []
    cached = {f"{c}.TWO": history_for(DAYS) for c in UNIVERSE}

    collect(make_market(requested, suffix=".TW"), cached)

    assert requested == DAYS[:-1], "後綴不符的快取應被忽略並觸發回補"


def test_missing_day_without_data_is_skipped_not_fatal() -> None:
    """行事曆判為交易日但交易所無資料時略過該日，不中斷整次執行。"""
    requested: list[date] = []
    market = make_market(requested, responses={DAYS[0]: {}})

    quotes = collect(market, {})

    assert DAYS[0] not in quotes
    assert DAYS[1] in quotes
    assert MARKET_DATE in quotes


def test_missing_market_day_does_not_starve_every_stock() -> None:
    """交易所某個交易日無資料時，仍必須湊滿 DEFAULT_DAYS 根 K 棒。

    這是實機執行時真的踩到的問題：2026-07-10 因颱風巴威休市，行事曆卻判為
    交易日。若候選日剛好只取 DEFAULT_DAYS 個，每一檔都會停在 29 根、卡在
    可靠門檻下一格，整個系統再也不發出任何訊號，而且 exit code 是 0、
    log 沒有錯誤。
    """
    assert TRADING_DAY_BUFFER >= 1, "必須有緩衝才能吸收無行情的交易日"

    start = date(2026, 6, 1)
    candidates = [start + timedelta(days=i) for i in range(DEFAULT_DAYS + TRADING_DAY_BUFFER)]
    blank = candidates[3]

    requested: list[date] = []
    market = make_market(requested, responses={blank: {}})

    quotes = collect(market, {}, trading_days=candidates, market_date=candidates[-1])

    usable = [d for d in candidates if quotes.get(d, {}).get("A") is not None]
    assert blank not in usable
    assert len(usable) >= DEFAULT_DAYS, (
        f"只湊到 {len(usable)} 根 K 棒，不足 {DEFAULT_DAYS}，所有個股都會被判為資料不足"
    )


def test_stores_full_candidate_window_not_just_the_analysis_window() -> None:
    """儲存整個候選區間，否則最舊的緩衝天數永遠不在快取裡。

    只存 DEFAULT_DAYS 的話，候選區間比儲存區間多出的那幾天每次執行都要
    重新抓取 —— 每天白付數十秒的限流等待，而且看起來一切正常。
    """
    from stock_notify.pipeline import _analyse_market
    from stock_notify.sources.common import PeInfo

    start = date(2026, 6, 1)
    candidates = [start + timedelta(days=i) for i in range(DEFAULT_DAYS + TRADING_DAY_BUFFER)]
    quotes = {d: bars_for(["A"], d) for d in candidates}

    stored: dict[str, PriceHistory] = {}

    class RecordingRepository:
        def load_all(self) -> dict[str, PriceHistory]:
            return {}

        def stage(self, symbol: str, history: PriceHistory) -> None:
            stored[symbol] = history

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    class _Settings:
        volume_multiplier = 2.0

    analyses = _analyse_market(
        make_market([]),
        {"A": PeInfo(name="測試", display="15.0")},
        candidates,
        quotes,
        RecordingRepository(),  # type: ignore[arg-type]
        _Settings(),  # type: ignore[arg-type]
    )

    assert len(stored["A.TW"]) == len(candidates), "應儲存整個候選區間"
    assert analyses[0].data_length == DEFAULT_DAYS, "分析只使用最後 DEFAULT_DAYS 根"


# --------------------------------------------------------------------------
# 取得當日行情：兩個端點的更新時間不同步
# --------------------------------------------------------------------------


def market_with_endpoints(
    latest: tuple[date, dict[str, Bar]],
    dated: dict[date, dict[str, Bar]],
    dated_calls: list[date],
) -> Market:
    def fetch_latest(session: object) -> tuple[date, dict[str, Bar]]:
        return latest

    def fetch_dated(session: object, day: date) -> dict[str, Bar]:
        dated_calls.append(day)
        return dated.get(day, {})

    def unused(*args: object, **kwargs: object) -> object:
        raise AssertionError("不應被呼叫")

    return Market(
        name="測試市場",
        suffix=".TW",
        fetch_pe=unused,  # type: ignore[arg-type]
        fetch_latest_quotes=fetch_latest,
        fetch_quotes_for_date=fetch_dated,
    )


def test_uses_cheap_endpoint_when_it_is_current() -> None:
    """免限流的端點已涵蓋目標日期時，不該多發一個受限流的請求。"""
    from stock_notify.pipeline import _fetch_quotes_for_day

    today = date(2026, 7, 27)
    calls: list[date] = []
    market = market_with_endpoints((today, bars_for(["A"], today)), {}, calls)

    bars = _fetch_quotes_for_day(None, market, today)  # type: ignore[arg-type]

    assert "A" in bars
    assert calls == [], "不該退回日期查詢"


def test_falls_back_when_cheap_endpoint_is_stale() -> None:
    """openapi 端點落後時必須改用日期查詢，而不是中止執行。

    這是 2026-07-27 排程失敗的原因：收盤後 5 小時，證交所的 STOCK_DAY_ALL
    仍停留在 07-24（落後 3 天），但同一時間日期查詢端點早已有當日資料。
    當時的程式直接判定「收盤資料尚未公布」而 exit 1，整天沒有發出通知。
    """
    from stock_notify.pipeline import _fetch_quotes_for_day

    today = date(2026, 7, 27)
    stale = date(2026, 7, 24)
    calls: list[date] = []
    market = market_with_endpoints(
        (stale, bars_for(["OLD"], stale)),
        {today: bars_for(["A", "B"], today)},
        calls,
    )

    bars = _fetch_quotes_for_day(None, market, today)  # type: ignore[arg-type]

    assert calls == [today]
    assert set(bars) == {"A", "B"}
    assert "OLD" not in bars, "不可回退成舊日期的行情，那會用過期價格產生訊號"


def test_raises_when_neither_endpoint_has_the_day() -> None:
    """兩個端點都沒有資料才是真的「尚未公布」，此時中止是正確的 ——
    用前一交易日的收盤價產生買賣訊號比不發通知更糟。"""
    from stock_notify.pipeline import _fetch_quotes_for_day
    from stock_notify.sources.common import DataSourceError

    today = date(2026, 7, 27)
    stale = date(2026, 7, 24)
    calls: list[date] = []
    market = market_with_endpoints((stale, bars_for(["OLD"], stale)), {}, calls)

    with pytest.raises(DataSourceError, match="尚無行情資料"):
        _fetch_quotes_for_day(None, market, today)  # type: ignore[arg-type]

    assert calls == [today], "應確實嘗試過日期查詢才放棄"
