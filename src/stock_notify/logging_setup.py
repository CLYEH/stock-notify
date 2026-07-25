"""logging 設定。

舊版用 92 個 print 混在判斷邏輯裡，每檔股票數行、一次執行數千行且無法過濾。
改為逐檔明細走 DEBUG、流程與結果走 INFO，預設只顯示 INFO 以上。
需要逐檔明細時設 `LOG_LEVEL=DEBUG`。
"""

from __future__ import annotations

import logging
import os


def configure_logging(level: str | None = None) -> None:
    resolved = level or os.environ.get("LOG_LEVEL") or "INFO"
    logging.basicConfig(
        level=resolved.upper(),
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
