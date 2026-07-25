"""命令列進入點。

回傳明確的 exit code —— 舊版把所有例外吞在 `main()` 裡只印 traceback，
程式永遠以 0 結束，GitHub Actions 因此在完全失敗時仍顯示綠燈。
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

from stock_notify.config import ConfigError, load_settings
from stock_notify.logging_setup import configure_logging
from stock_notify.notify.line import NotificationError
from stock_notify.pipeline import run
from stock_notify.sources.common import DataSourceError
from stock_notify.trading_calendar import TradingCalendarError

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CONFIG_ERROR = 2
EXIT_INTERRUPTED = 130


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="stock-notify", description="台股 PE + KDJ 分析通知")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="以指定日期執行（驗證用，預設為今天）",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="要載入的 .env 檔；亦可用環境變數 STOCK_ENV_FILE 指定",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_logging()

    try:
        settings = load_settings(args.env_file)
    except ConfigError as exc:
        logger.error("設定錯誤：%s", exc)
        return EXIT_CONFIG_ERROR

    try:
        run(settings, today=args.date or date.today())
    except TradingCalendarError as exc:
        logger.error("%s；為避免在休市日發出買賣訊號，本次不執行分析", exc)
        return EXIT_FAILURE
    except (DataSourceError, NotificationError) as exc:
        logger.error("%s", exc)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        logger.warning("使用者中斷")
        return EXIT_INTERRUPTED
    except Exception:
        logger.exception("未預期的錯誤")
        return EXIT_FAILURE

    return EXIT_OK
