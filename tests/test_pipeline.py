"""行情組裝邏輯。

這一層決定要對證交所發出幾個請求。每個歷史請求都要等 6 秒，
多抓是浪費、少抓則會用殘缺的序列算 KDJ，兩種錯誤都很貴。
"""

from __future__ import annotations

from datetime import date

import pytest

from stock_notify.models import Bar, PriceHistory
from stock_notify.pipeline import _collect_quotes
from stock_notify.storage.mongo import NullPriceRepository

DAYS = [date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22)]
MARKET_DATE = DAYS[-1]


class FakeRepository(NullPriceRepository):
    def __init__(self, histories: dict[str, PriceHistory]) -> None:
        self._histories = histories

    def load_all(self) -> dict[str, PriceHistory]:
        return self._histories


class NoThrottle:
    def wait(self) -> None:
        return None


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stock_notify.pipeline.Throttle", NoThrottle)


def history_for(days: list[date]) -> PriceHistory:
    return PriceHistory(
        dates=list(days),
        open=[10.0] * len(days),
        high=[11.0] * len(days),
        low=[9.0] * len(days),
        close=[10.5] * len(days),
        volume=[100] * len(days),
    )


def bars_for(codes: list[str], day: date) -> dict[str, Bar]:
    return {c: Bar(date=day, open=1.0, high=2.0, low=0.5, close=1.5, volume=10) for c in codes}


def collect(
    monkeypatch: pytest.MonkeyPatch,
    repository: NullPriceRepository,
    universe_size: int,
    fetched: dict[date, dict[str, Bar]] | None = None,
) -> tuple[dict[date, dict[str, Bar]], list[date]]:
    """執行 _collect_quotes 並回傳實際發出請求的日期。"""
    requested: list[date] = []

    def fake_fetch(session: object, day: date) -> dict[str, Bar]:
        requested.append(day)
        return (fetched or {}).get(day, bars_for(["A", "B", "C"], day))

    monkeypatch.setattr("stock_notify.pipeline.fetch_quotes_for_date", fake_fetch)

    quotes = _collect_quotes(
        session=None,  # type: ignore[arg-type]
        repository=repository,
        trading_days=DAYS,
        market_date=MARKET_DATE,
        latest_bars=bars_for(["A", "B", "C"], MARKET_DATE),
        universe_size=universe_size,
    )
    return quotes, requested


def test_fully_cached_history_needs_no_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """穩態下每天只需要最新一日的行情，歷史全部來自資料庫。"""
    repo = FakeRepository({code: history_for(DAYS) for code in ("A", "B", "C")})
    quotes, requested = collect(monkeypatch, repo, universe_size=3)

    assert requested == []
    assert set(quotes) == set(DAYS)


def test_empty_cache_backfills_every_day_except_the_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """冷啟動時最新一日已由 STOCK_DAY_ALL 取得，不重複請求。"""
    _, requested = collect(monkeypatch, FakeRepository({}), universe_size=3)

    assert requested == DAYS[:-1]
    assert MARKET_DATE not in requested


def test_thinly_covered_day_is_refetched(monkeypatch: pytest.MonkeyPatch) -> None:
    """快取只涵蓋少數個股的日期要重抓。

    否則新上市個股永遠湊不滿 30 天，每天都被判為資料不足而無法產生訊號。
    """
    repo = FakeRepository({"A": history_for(DAYS)})  # 3 檔中只有 1 檔有歷史
    _, requested = collect(monkeypatch, repo, universe_size=3)

    assert DAYS[0] in requested
    assert DAYS[1] in requested


def test_missing_day_without_data_is_skipped_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """行事曆判為交易日但證交所無資料時略過該日，不中斷整次執行。"""
    quotes, requested = collect(
        monkeypatch,
        FakeRepository({}),
        universe_size=3,
        fetched={DAYS[0]: {}, DAYS[1]: bars_for(["A", "B", "C"], DAYS[1])},
    )

    assert DAYS[0] not in quotes
    assert DAYS[1] in quotes
    assert MARKET_DATE in quotes


def test_missing_market_day_does_not_starve_every_stock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """證交所某個交易日無資料時，仍必須湊滿 DEFAULT_DAYS 根 K 棒。

    這是實機執行時真的踩到的問題：2026-07-10 行事曆判為交易日但證交所無行情，
    若候選日剛好只取 DEFAULT_DAYS 個，每一檔都會停在 29 根、卡在可靠門檻下一格，
    整個系統再也不發出任何訊號，而且 exit code 是 0、log 沒有錯誤。
    """
    from datetime import timedelta

    from stock_notify.config import DEFAULT_DAYS, TRADING_DAY_BUFFER

    assert TRADING_DAY_BUFFER >= 1, "必須有緩衝才能吸收無行情的交易日"

    start = date(2026, 6, 1)
    candidates = [start + timedelta(days=i) for i in range(DEFAULT_DAYS + TRADING_DAY_BUFFER)]
    blank = candidates[3]

    def fake_fetch(session: object, day: date) -> dict[str, Bar]:
        return {} if day == blank else bars_for(["A"], day)

    monkeypatch.setattr("stock_notify.pipeline.fetch_quotes_for_date", fake_fetch)

    quotes = _collect_quotes(
        session=None,  # type: ignore[arg-type]
        repository=NullPriceRepository(),
        trading_days=candidates,
        market_date=candidates[-1],
        latest_bars=bars_for(["A"], candidates[-1]),
        universe_size=1,
    )

    usable = [d for d in candidates if quotes.get(d, {}).get("A") is not None]
    assert blank not in usable
    assert len(usable) >= DEFAULT_DAYS, (
        f"只湊到 {len(usable)} 根 K 棒，不足 {DEFAULT_DAYS}，所有個股都會被判為資料不足"
    )
