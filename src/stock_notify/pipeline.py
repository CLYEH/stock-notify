"""流程編排。

取代舊版 129 行的 `run_analysis`。本模組只負責串接各層，
所有判斷邏輯都在 `analysis`、`trading_calendar`、`notify.formatting` 裡。

資料取得策略（詳見 `sources.twse` / `sources.tpex`）：全市場端點一次拿回
所有個股，每個市場每日只需 2 個 request；僅在資料庫缺歷史時才逐日回補，
並遵守各主機獨立的頻率限制。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import requests

from stock_notify.analysis.signals import analyze_stock
from stock_notify.config import (
    DEFAULT_DAYS,
    PROGRESS_REPORT_INTERVAL,
    TRADING_DAY_BUFFER,
    Settings,
)
from stock_notify.models import Bar, PriceHistory, Signal, StockAnalysis
from stock_notify.notify.formatting import (
    format_market_closed,
    format_no_signal,
    format_signal_report,
)
from stock_notify.notify.line import Notifier, create_notifier
from stock_notify.sources.common import DataSourceError, PeInfo, Throttle
from stock_notify.sources.market import MARKETS, Market
from stock_notify.storage.mongo import PriceRepository, create_repository
from stock_notify.trading_calendar import (
    HolidayRecord,
    closure_for,
    fetch_holiday_calendar,
    parse_calendar,
    recent_trading_days,
)

logger = logging.getLogger(__name__)

MIN_CACHE_COVERAGE = 0.8
"""快取中某一交易日的個股覆蓋率低於此比例時重新抓取該日。

否則新上市個股會永遠停在殘缺的歷史，每天都湊不滿 30 天而無法產生訊號。
"""

QuotesByDate = dict[date, dict[str, Bar]]


@dataclass(frozen=True)
class RunResult:
    """一次執行的摘要。"""

    market_date: date
    analyzed: int
    buys: int
    sells: int


def run(settings: Settings, today: date) -> RunResult | None:
    """執行一次完整分析。休市時發出休市通知並回傳 None。"""
    session = requests.Session()
    notifier = create_notifier(settings.line_token, settings.line_user_id)

    calendar = parse_calendar(fetch_holiday_calendar(session))
    closure = closure_for(calendar, today)
    if closure is not None:
        logger.info("今日休市：%s (%s)", closure.name, closure.category)
        notifier.send(format_market_closed(closure.name, closure.category))
        return None

    logger.info("今日為交易日，開始分析")
    repository = create_repository(settings.mongo_uri)
    try:
        return _analyse_and_notify(session, settings, calendar, today, notifier, repository)
    finally:
        repository.close()


def _analyse_and_notify(
    session: requests.Session,
    settings: Settings,
    calendar: dict[date, HolidayRecord],
    today: date,
    notifier: Notifier,
    repository: PriceRepository,
) -> RunResult:
    cached = repository.load_all()
    analyses: list[StockAnalysis] = []
    market_dates: list[date] = []

    for market in MARKETS:
        pe_data = market.fetch_pe(session)
        if not pe_data:
            raise DataSourceError(f"{market.name}本益比資料為空，無法決定分析標的")

        market_date, latest_bars = market.fetch_latest_quotes(session)
        if market_date < today:
            raise DataSourceError(
                f"今日 ({today.isoformat()}) 為交易日，但{market.name}最新行情仍停留在 "
                f"{market_date.isoformat()}，收盤資料尚未公布"
            )
        market_dates.append(market_date)

        # 多取幾天作為緩衝：行事曆說是交易日、交易所卻無行情的日子會讓每一檔都少一根
        # K 棒，若無緩衝會整批卡在 KDJ 可靠門檻下一格而永遠不發訊號。
        trading_days = recent_trading_days(calendar, market_date, DEFAULT_DAYS + TRADING_DAY_BUFFER)
        logger.info(
            "%s候選區間 %s ~ %s，共 %d 個交易日（每檔取最後 %d 根 K 棒）",
            market.name,
            trading_days[0].isoformat(),
            trading_days[-1].isoformat(),
            len(trading_days),
            DEFAULT_DAYS,
        )

        quotes = _collect_quotes(
            session=session,
            market=market,
            cached=cached,
            trading_days=trading_days,
            market_date=market_date,
            latest_bars=latest_bars,
            universe=set(pe_data),
        )
        analyses.extend(
            _analyse_market(market, pe_data, trading_days, quotes, repository, settings)
        )

    repository.flush()

    buys = sum(1 for a in analyses if a.signal is Signal.BUY)
    sells = sum(1 for a in analyses if a.signal is Signal.SELL)
    logger.info("分析完成：%d 檔 | 買進 %d | 賣出 %d", len(analyses), buys, sells)

    report_date = max(market_dates)
    if buys or sells:
        notifier.send(format_signal_report(analyses, report_date))
    else:
        notifier.send(format_no_signal(len(analyses), report_date))

    return RunResult(market_date=report_date, analyzed=len(analyses), buys=buys, sells=sells)


def _collect_quotes(
    *,
    session: requests.Session,
    market: Market,
    cached: dict[str, PriceHistory],
    trading_days: list[date],
    market_date: date,
    latest_bars: dict[str, Bar],
    universe: set[str],
) -> QuotesByDate:
    """組出分析區間內每個交易日的全市場行情。

    先用資料庫既有的歷史填滿，再對仍然缺漏的日期逐日回補。
    """
    quotes: QuotesByDate = {}
    wanted = set(trading_days)

    for symbol, history in cached.items():
        if not symbol.endswith(market.suffix):
            continue
        code = symbol[: -len(market.suffix)]
        for index, day in enumerate(history.dates):
            if day not in wanted:
                continue
            quotes.setdefault(day, {})[code] = Bar(
                date=day,
                open=history.open[index],
                high=history.high[index],
                low=history.low[index],
                close=history.close[index],
                volume=history.volume[index],
            )

    quotes[market_date] = latest_bars

    # 只計入分析標的的覆蓋率；行情端點常含 ETF 與權證，全部計入會高估
    minimum = len(universe) * MIN_CACHE_COVERAGE
    missing = [
        day
        for day in trading_days
        if sum(1 for code in quotes.get(day, {}) if code in universe) < minimum
    ]

    if missing:
        logger.info(
            "%s需回補 %d 個交易日（每個請求間隔 6 秒，預估 %.0f 秒）",
            market.name,
            len(missing),
            len(missing) * 6.0,
        )
        throttle = Throttle()
        for day in missing:
            throttle.wait()
            bars = market.fetch_quotes_for_date(session, day)
            if not bars:
                logger.warning("%s %s 無行情資料，略過", market.name, day.isoformat())
                continue
            quotes[day] = bars
            logger.info("%s回補 %s：%d 檔", market.name, day.isoformat(), len(bars))

    return quotes


def _analyse_market(
    market: Market,
    pe_data: dict[str, PeInfo],
    trading_days: list[date],
    quotes: QuotesByDate,
    repository: PriceRepository,
    settings: Settings,
) -> list[StockAnalysis]:
    analyses: list[StockAnalysis] = []

    for index, (code, pe_info) in enumerate(sorted(pe_data.items()), start=1):
        bars = [bar for day in trading_days if (bar := quotes.get(day, {}).get(code)) is not None]

        # 儲存整個候選區間（含緩衝天數），而非只存分析用的 DEFAULT_DAYS。
        # 只存 30 天的話，候選區間最舊的那幾天永遠不在快取裡，
        # 每次執行都要重新抓取，等於白付數十秒的限流等待。
        repository.stage(market.symbol(code), PriceHistory.from_bars(bars))

        analysis = analyze_stock(
            code=code,
            name=pe_info.name,
            pe_display=pe_info.display,
            prices=PriceHistory.from_bars(bars[-DEFAULT_DAYS:]),
            volume_multiplier=settings.volume_multiplier,
        )
        analyses.append(analysis)

        if analysis.signal in (Signal.BUY, Signal.SELL):
            logger.info(
                "%s %s %s (%s) | PE %s | J %s → %s",
                "🔴 買進" if analysis.signal is Signal.BUY else "🔵 賣出",
                market.name,
                analysis.name,
                analysis.code,
                analysis.pe_display,
                analysis.yesterday_j,
                analysis.j_value,
            )

        if index % PROGRESS_REPORT_INTERVAL == 0:
            logger.debug("%s已分析 %d/%d 檔", market.name, index, len(pe_data))

    return analyses
