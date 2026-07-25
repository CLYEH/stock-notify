"""通知訊息格式化。

全部是純函式，不做 I/O，因此訊息內容可以直接測試。舊版把訊息組裝散在
`run_analysis`（無訊號訊息）與 `LineNotifier`（有訊號訊息）兩處，且買進與
賣出兩段是近乎逐字複製的程式碼。

訊息中的門檻數字一律取自 config，避免文案與實際判斷邏輯各自漂移。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from stock_notify.config import (
    J_BUY_THRESHOLD,
    J_SELL_THRESHOLD,
    PE_BUY_THRESHOLD,
    PE_SELL_THRESHOLD,
)
from stock_notify.models import Signal, StockAnalysis

VOLUME_SPIKE_MARK = "*"
VOLUME_SPIKE_LEGEND = f"{VOLUME_SPIKE_MARK} 表示成交量異常放大"


def _format_stock_line(analysis: StockAnalysis) -> str:
    line = f"{analysis.name} {analysis.code}"
    if analysis.volume_spike:
        line += f" {VOLUME_SPIKE_MARK}"

    details = []
    if analysis.pe_display is not None:
        details.append(f"PE: {analysis.pe_display}")
    if analysis.j_value is not None:
        details.append(f"J: {analysis.j_value:.1f}")

    if details:
        line += f" ({', '.join(details)})"
    return line


def _format_section(title: str, analyses: list[StockAnalysis]) -> list[str]:
    return [title, *(_format_stock_line(a) for a in analyses)]


def format_signal_report(analyses: Iterable[StockAnalysis], today: date) -> str:
    """組出含買賣建議的通知訊息。"""
    buys = [a for a in analyses if a.signal is Signal.BUY]
    sells = [a for a in analyses if a.signal is Signal.SELL]

    if not buys and not sells:
        return "📊 今日股票分析完成\n無符合條件的買賣建議"

    parts = [f"📊 股票分析 v2 ({today.isoformat()})", ""]

    if buys:
        parts.extend(_format_section("🔴 買進建議", buys))
        parts.append("")
    if sells:
        parts.extend(_format_section("🔵 賣出建議", sells))

    if any(a.volume_spike for a in buys + sells):
        parts.extend(["", VOLUME_SPIKE_LEGEND])

    return "\n".join(parts)


def format_no_signal(total_analyzed: int, today: date) -> str:
    """今日沒有任何買賣建議時的訊息。"""
    return "\n".join(
        [
            f"📊 股票分析完成 v2 ({today.isoformat()})",
            "",
            "今日無符合條件的買賣建議",
            "",
            "分析條件 (v2趨勢突破):",
            f"• 買進: 昨日J>{J_BUY_THRESHOLD:g}→今日J<{J_BUY_THRESHOLD:g}"
            f" 且 PE<{PE_BUY_THRESHOLD:g}",
            f"• 賣出: 昨日J<{J_SELL_THRESHOLD:g}→今日J>{J_SELL_THRESHOLD:g}"
            f" 且 PE>{PE_SELL_THRESHOLD:g}",
            f"• 總計分析: {total_analyzed} 檔股票",
        ]
    )


def format_market_closed(name: str, category: str) -> str:
    """休市通知。"""
    return f"📅 台股休市通知\n\n今天是{name} ({category})，因此沒有開盤，交易暫停一日。"
