"""通知訊息格式。

訊息是這個系統唯一的對外產出，格式是使用者實際看到的東西。
重構前後必須逐字相同，因此以完整字串比對而非片段檢查。
"""

from __future__ import annotations

from datetime import date

from stock_notify.models import Signal, StockAnalysis
from stock_notify.notify.formatting import (
    format_market_closed,
    format_no_signal,
    format_signal_report,
)

TODAY = date(2026, 7, 24)


def analysis(
    code: str,
    name: str,
    signal: Signal,
    pe: str | None = "15.00",
    j: float | None = 5.24,
    spike: bool = False,
) -> StockAnalysis:
    return StockAnalysis(
        code=code,
        name=name,
        signal=signal,
        pe_ratio=float(pe) if pe else None,
        pe_display=pe,
        j_value=j,
        yesterday_j=12.0,
        volume_spike=spike,
        data_length=30,
    )


def test_buy_and_sell_sections() -> None:
    report = format_signal_report(
        [
            analysis("2330", "台積電", Signal.BUY, "31.59", 5.24),
            analysis("2002", "中鋼", Signal.SELL, "50.00", 95.31),
            analysis("1101", "台泥", Signal.HOLD),
        ],
        TODAY,
    )

    assert report == (
        "📊 股票分析 v2 (2026-07-24)\n"
        "\n"
        "🔴 買進建議\n"
        "台積電 2330 (PE: 31.59, J: 5.2)\n"
        "\n"
        "🔵 賣出建議\n"
        "中鋼 2002 (PE: 50.00, J: 95.3)"
    )


def test_hold_stocks_are_excluded() -> None:
    report = format_signal_report([analysis("1101", "台泥", Signal.HOLD)], TODAY)
    assert report == "📊 今日股票分析完成\n無符合條件的買賣建議"


def test_volume_spike_marker_and_legend() -> None:
    """成交量放大的股票加註 `*`，並且只在真的有標記時才附上說明。"""
    with_spike = format_signal_report([analysis("2330", "台積電", Signal.BUY, spike=True)], TODAY)
    assert "台積電 2330 * (PE: 15.00, J: 5.2)" in with_spike
    assert with_spike.endswith("\n\n* 表示成交量異常放大")

    without_spike = format_signal_report([analysis("2330", "台積電", Signal.BUY)], TODAY)
    assert "*" not in without_spike


def test_pe_text_is_not_reformatted() -> None:
    """證交所回傳 "12.30"，不能被格式化成 "12.3"。"""
    report = format_signal_report([analysis("2330", "台積電", Signal.BUY, "12.30")], TODAY)
    assert "PE: 12.30" in report


def test_missing_details_are_omitted() -> None:
    report = format_signal_report([analysis("2330", "台積電", Signal.BUY, pe=None, j=None)], TODAY)
    assert report.splitlines()[-1] == "台積電 2330", "沒有 PE 與 J 時不應留下空的括號"


def test_no_signal_message_states_the_actual_rule() -> None:
    """文案中的門檻取自 config，避免與實際判斷邏輯漂移。"""
    message = format_no_signal(1080, TODAY)
    assert message == (
        "📊 股票分析完成 v2 (2026-07-24)\n"
        "\n"
        "今日無符合條件的買賣建議\n"
        "\n"
        "分析條件 (v2趨勢突破):\n"
        "• 買進: 昨日J>10→今日J<10 且 PE<20\n"
        "• 賣出: 昨日J<90→今日J>90 且 PE>40\n"
        "• 總計分析: 1080 檔股票"
    )


def test_market_closed_message() -> None:
    assert format_market_closed("中華民國開國紀念日", "放假之紀念日及節日") == (
        "📅 台股休市通知\n\n"
        "今天是中華民國開國紀念日 (放假之紀念日及節日)，因此沒有開盤，交易暫停一日。"
    )
