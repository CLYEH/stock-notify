"""
Monkey patch twstock 以支援台灣證交所 2024/12 新增的「權值」欄位。

原本的 twstock DATATUPLE 只有 9 個欄位，新版 API 回傳 10 個欄位，
未 patch 時 twstock 會直接 raise。本模組將 DATATUPLE 與 TWSEFetcher /
TPEXFetcher 的 _make_datatuple 改為支援第 10 個欄位 (weight)。

呼叫方式：
    from stock_tool.twstock_patch import apply_twstock_patch
    apply_twstock_patch()   # 必須在 `import twstock` 之前呼叫
"""

from collections import namedtuple
from datetime import datetime


PATCHED_DATATUPLE = namedtuple(
    "Data",
    [
        "date",
        "capacity",
        "turnover",
        "open",
        "high",
        "low",
        "close",
        "change",
        "transaction",
        "weight",
    ],
)


def _patched_twse_make_datatuple(self, data):
    data[0] = datetime.strptime(self._convert_date(data[0]), "%Y/%m/%d")
    data[1] = int(data[1].replace(",", ""))
    data[2] = int(data[2].replace(",", ""))
    data[3] = None if data[3] == "--" else float(data[3].replace(",", ""))
    data[4] = None if data[4] == "--" else float(data[4].replace(",", ""))
    data[5] = None if data[5] == "--" else float(data[5].replace(",", ""))
    data[6] = None if data[6] == "--" else float(data[6].replace(",", ""))
    data[7] = float(
        0.0 if data[7].replace(",", "") == "X0.00" else data[7].replace(",", "")
    )
    data[8] = int(data[8].replace(",", ""))
    data[9] = data[9] if len(data) > 9 else ""
    return PATCHED_DATATUPLE(*data[:10])


def _patched_tpex_make_datatuple(self, data):
    data[0] = datetime.strptime(
        self._convert_date(data[0].replace("＊", "")), "%Y/%m/%d"
    )
    data[1] = int(data[1].replace(",", "")) * 1000
    data[2] = int(data[2].replace(",", "")) * 1000
    data[3] = None if data[3] == "--" else float(data[3].replace(",", ""))
    data[4] = None if data[4] == "--" else float(data[4].replace(",", ""))
    data[5] = None if data[5] == "--" else float(data[5].replace(",", ""))
    data[6] = None if data[6] == "--" else float(data[6].replace(",", ""))
    data[7] = float(data[7].replace(",", ""))
    data[8] = int(data[8].replace(",", ""))
    data_weight = data[9] if len(data) > 9 else ""
    return PATCHED_DATATUPLE(*data[:9], data_weight)


def apply_twstock_patch():
    """套用 monkey patch。必須在 `import twstock` 之前呼叫。"""
    import twstock.stock as twstock_stock

    twstock_stock.DATATUPLE = PATCHED_DATATUPLE
    twstock_stock.TWSEFetcher._make_datatuple = _patched_twse_make_datatuple
    twstock_stock.TPEXFetcher._make_datatuple = _patched_tpex_make_datatuple
    print("✅ twstock monkey patch 已套用 (支援新「權值」欄位)")
