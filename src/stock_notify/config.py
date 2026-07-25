"""集中管理常數與環境變數設定。

模組層級常數為固定的業務參數；`load_settings()` 負責讀取環境變數，
刻意寫成函式而非 import 時執行，避免匯入本模組就產生副作用。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# ---- 外部端點 ----
HOLIDAY_API_URL = (
    "https://data.ntpc.gov.tw/api/datasets/308dcd75-6434-45bc-a95f-584da4fed251/csv/file"
)
PE_DATA_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
STOCK_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
"""最新交易日的全市場 OHLCV，一個 request 涵蓋所有個股。"""
MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
"""指定日期的全市場 OHLCV，僅用於歷史回補。受 6 秒頻率限制。"""

TPEX_PE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
TPEX_DAILY_QUOTES_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TPEX_HISTORY_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"
"""上櫃歷史行情。主機與證交所不同，限流額度獨立。"""
LINE_PUSH_API_URL = "https://api.line.me/v2/bot/message/push"

# ---- 分析參數 ----
KDJ_PERIOD = 9
"""KDJ 的 RSV 計算窗格，同時也是可計算 KDJ 的最小資料長度。"""
KDJ_SUFFICIENT_LENGTH = 30
"""低於此天數的 KDJ 視為不可靠，不產生任何買賣訊號。"""
J_BUY_THRESHOLD = 10.0
J_SELL_THRESHOLD = 90.0
PE_BUY_THRESHOLD = 20.0
PE_SELL_THRESHOLD = 40.0

# ---- 資料抓取參數 ----
DEFAULT_DAYS = 30
"""每檔股票保留的交易日數（滑動窗口長度）。"""
TRADING_DAY_BUFFER = 5
"""向行事曆多要幾個候選交易日作為緩衝。

行事曆判定為交易日、證交所卻無行情的情形確實會發生（例如颱風假不在
新北市行事曆中）。若剛好只取 DEFAULT_DAYS 個候選日，少一天就會讓
**每一檔**股票都停在 29 根 K 棒、卡在 KDJ_SUFFICIENT_LENGTH 門檻下一格，
導致整個系統再也不發出任何訊號，而且沒有任何錯誤跡象。
"""
BACKFILL_MAX_CALENDAR_DAYS = 90
"""回補歷史時最多往回查看的日曆天數，避免行事曆異常導致無止盡回溯。"""
TWSE_REQUEST_INTERVAL_SECONDS = 6.0
"""www.twse.com.tw 的請求間隔。實測門檻約 6 秒，超過會鎖 IP 一小時。

僅套用於歷史回補；每日更新走 openapi.twse.com.tw，總共只有 2 個 request。
"""
PROGRESS_REPORT_INTERVAL = 100
HTTP_TIMEOUT_SECONDS = 30
HOLIDAY_API_TIMEOUT_SECONDS = 10
HOLIDAY_API_ATTEMPTS = 3
"""假日 API 的總嘗試次數。全部失敗即視為無法確認交易日，中止執行。"""

MONGO_DB_NAME = "StockStrategy"
MONGO_COLLECTION_NAME = "twstock"


class ConfigError(Exception):
    """環境變數缺失或格式錯誤。"""


@dataclass(frozen=True)
class Settings:
    """由環境變數解析出的執行期設定。"""

    line_token: str | None
    line_user_id: str | None
    mongo_uri: str | None
    volume_multiplier: float

    @property
    def line_enabled(self) -> bool:
        return bool(self.line_token and self.line_user_id)

    @property
    def mongo_enabled(self) -> bool:
        return bool(self.mongo_uri)


def load_settings(env_file: str | None = None) -> Settings:
    """載入 .env 並解析環境變數。

    Args:
        env_file: 要載入的 .env 檔路徑。未指定時取 `STOCK_ENV_FILE`，
            再退回 `.env`。測試時可用 `.env.development` 改發給個人帳號。

    Raises:
        ConfigError: 環境變數格式錯誤。
    """
    load_dotenv(env_file or os.getenv("STOCK_ENV_FILE", ".env"))

    raw_multiplier = os.getenv("VOLUME_MULTIPLIER", "2.0")
    try:
        volume_multiplier = float(raw_multiplier)
    except ValueError as exc:
        raise ConfigError(f"VOLUME_MULTIPLIER 必須是數字，實際收到 {raw_multiplier!r}") from exc

    return Settings(
        line_token=os.getenv("LINE_TOKEN") or None,
        line_user_id=os.getenv("LINE_USER_ID") or None,
        mongo_uri=os.getenv("MONGO_URI") or None,
        volume_multiplier=volume_multiplier,
    )
