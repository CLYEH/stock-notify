"""
台股分析通知系統主程式 v2
整合 PE ratio、KDJ 技術指標分析，並透過 LINE 發送通知
J值判斷邏輯：昨日大於10，今日小於10 (買進)；昨日小於90，今日大於90 (賣出)
"""

import os
import sys
import csv
import time
import requests
import pandas as pd
from io import StringIO
from datetime import datetime, timedelta
from pymongo import MongoClient, UpdateOne
from pymongo.server_api import ServerApi
from dotenv import load_dotenv

# 加入 src 路徑（必須在 import stock_tool.* 之前）
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

# twstock monkey patch 必須在 import twstock 之前套用
from stock_tool.twstock_patch import apply_twstock_patch
apply_twstock_patch()

import twstock

# 載入環境變數
load_dotenv()

from stock_tool.pe import PERatioAnalyzer
from stock_tool.kdj import KDJAnalyzer
from notify import LineNotifier


# ---- 設定 ----
HOLIDAY_API_URL = (
    'https://data.ntpc.gov.tw/api/datasets/'
    '308dcd75-6434-45bc-a95f-584da4fed251/csv/file'
)
PE_DATA_URL = 'https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL'
MONGO_URI_TEMPLATE = (
    "mongodb+srv://leoyeh906_db_user:{password}"
    "@cluster0.zwdnfad.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
)

DEFAULT_DAYS = 30                  # 預設取得天數
FETCH_LOOKBACK_DAYS = 60           # 從 twstock fetch_from 回溯天數
KDJ_MIN_LENGTH = 9                 # KDJ 計算所需最小資料長度
KDJ_SUFFICIENT_LENGTH = 30         # KDJ 可靠的資料長度門檻
DATA_FRESHNESS_DAYS = 3            # 資料新鮮度容忍天數
PROGRESS_REPORT_INTERVAL = 25      # 進度報告間隔


def check_if_holiday():
    """
    檢查今日是否為放假日
    從新北市政府開放資料API獲取放假日資料

    Returns:
        tuple: (is_holiday: bool, holiday_name: str, holiday_category: str)
    """
    try:
        print("🗓️ 正在檢查今日是否為放假日...")
        response = requests.get(HOLIDAY_API_URL, timeout=10)
        response.raise_for_status()

        csv_reader = csv.DictReader(StringIO(response.text))

        today_date = datetime.now().strftime('%Y%m%d')
        print(f"📅 今日日期: {today_date}")

        for row in csv_reader:
            # 處理 BOM 字符問題，CSV 第一個欄位可能是 '﻿date' 而不是 'date'
            date_value = row.get('date') or row.get('﻿date')
            if date_value != today_date:
                continue

            is_holiday = row['isholiday'] == '是'
            holiday_name = row['name'].strip() if row['name'] else ''
            holiday_category = row['holidaycategory'].strip() if row['holidaycategory'] else ''

            # 軍人節不休市（股市照常交易）
            if holiday_name == '軍人節':
                print("🪖 今日為軍人節，但股市照常交易")
                return False, '', ''

            if is_holiday:
                print(f"🎉 今日為放假日: {holiday_name or '週末/假日'} ({holiday_category})")
                return True, holiday_name or '週末/假日', holiday_category

            print("✅ 今日為工作日，繼續執行程式")
            return False, '', ''

        # 找不到今日資料，視為工作日
        print("⚠️ 未找到今日假期資料，視為工作日繼續執行")
        return False, '', ''

    except Exception as e:
        print(f"❌ 檢查放假日時發生錯誤: {e}")
        print("⚠️ 無法確認假期狀態，繼續執行程式")
        return False, '', ''


def _parse_market_date(value):
    """將字串/datetime 轉成 datetime 物件，無法解析時回傳 None。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, '%Y-%m-%d')
        except Exception:
            try:
                return pd.to_datetime(value).to_pydatetime()
            except Exception:
                return None
    try:
        return pd.to_datetime(value).to_pydatetime()
    except Exception:
        return None


def _extract_latest_market_date(doc_or_date):
    """從 dict 或單值中取出最新交易日字串/物件。"""
    if isinstance(doc_or_date, dict):
        return (
            doc_or_date.get('latest_data', {}).get('date')
            or doc_or_date.get('date')
        )
    return doc_or_date


class StockAnalysisSystemV2:
    def __init__(self):
        """初始化股票分析系統 v2"""
        print("🔧 正在初始化系統 v2...")
        print("🔄 v2 特色: J值變化趨勢判斷 (昨日>10今日<10買進, 昨日<90今日>90賣出)")

        # 初始化分析器
        print("📊 初始化分析器...")
        self.pe_analyzer = PERatioAnalyzer()
        self.kdj_analyzer = KDJAnalyzer()

        print("📱 初始化 LINE 通知器...")
        try:
            self.line_notifier = LineNotifier()
            print("✅ LINE 通知器初始化成功")
        except Exception as e:
            print(f"⚠️ LINE 通知器初始化失敗: {e}")
            self.line_notifier = None

        # MongoDB 連接
        self.mongo_client = None
        self.db = None
        print("🗄️ 初始化資料庫連接...")
        self.init_mongodb()

        # 成交量倍數設定 (可從環境變數調整)
        self.volume_multiplier = float(os.getenv('VOLUME_MULTIPLIER', '2.0'))
        print(f"📈 成交量倍數設定: {self.volume_multiplier}")

        # 重用 HTTP session 與快取
        self._http_session = requests.Session()
        self._stock_list_cache = None
        self._db_read_cache = {}
        self._pending_db_writes = []

        print("✅ 系統初始化完成")

    def init_mongodb(self):
        """初始化 MongoDB 連接"""
        try:
            mongo_password = os.getenv('MONGO_PASSWORD')
            if not mongo_password:
                print("⚠️ MONGO_PASSWORD 未設定，跳過資料庫連接")
                self.mongo_client = None
                self.db = None
                return

            mongo_uri = MONGO_URI_TEMPLATE.format(password=mongo_password)

            self.mongo_client = MongoClient(mongo_uri, server_api=ServerApi('1'))
            self.mongo_client.admin.command('ping')

            self.db = self.mongo_client['StockStrategy']

            print("✅ MongoDB 連接成功")

        except Exception as e:
            print(f"❌ MongoDB 連接失敗: {e}")
            self.mongo_client = None
            self.db = None

    def is_trading_day(self, date=None):
        """
        判斷指定日期是否為交易日

        Args:
            date (datetime): 要檢查的日期，預設為今天

        Returns:
            bool: True 如果是交易日
        """
        if date is None:
            date = datetime.now()
        # 簡單判斷：週一到週五，且不是國定假日
        # 這裡可以進一步整合台灣證交所的交易日曆 API
        return date.weekday() < 5

    def get_stock_list(self):
        """
        獲取要分析的股票清單（首次後快取）

        Returns:
            list: 股票代碼清單
        """
        if self._stock_list_cache is not None:
            return self._stock_list_cache
        try:
            stock_list = [code for code in twstock.twse if twstock.twse[code].type == '股票']
            print(f"✅ 取得股票清單，共 {len(stock_list)} 檔股票")
            self._stock_list_cache = stock_list
            return stock_list
        except Exception as e:
            print(f"❌ 獲取股票清單失敗: {e}")
            return []

    def fetch_pe_data(self):
        """
        從證交所 API 獲取 PE ratio 資料

        Returns:
            dict: 以股票代碼為 key 的 PE ratio 資料
        """
        print("🌐 正在從證交所 API 獲取 PE ratio 資料...")
        try:
            print(f"📡 發送請求到: {PE_DATA_URL}")
            response = self._http_session.get(PE_DATA_URL, timeout=30)
            response.raise_for_status()
            print("✅ API 請求成功，正在解析資料...")

            data = response.json()
            print(f"📋 收到 {len(data)} 筆原始資料，正在處理...")

            pe_data = {}
            for item in data:
                code = item.get('Code')
                pe_ratio = item.get('PEratio')
                if code and pe_ratio:
                    pe_data[code] = {
                        'pe_ratio': pe_ratio,
                        'name': item.get('Name'),
                    }

            print(f"✅ 取得 PE ratio 資料，共 {len(pe_data)} 檔股票 (有效資料: {len(pe_data)} 筆)")
            return pe_data

        except Exception as e:
            print(f"❌ 獲取 PE ratio 資料失敗: {e}")
            return {}

    def _preload_db_cache(self):
        """一次性將整個 twstock 集合載入記憶體，避免每檔股票一次 round-trip。"""
        if self.db is None:
            return
        try:
            cursor = self.db["twstock"].find({}).sort("updated_at", -1)
            cache = {}
            for doc in cursor:
                symbol = doc.get('symbol')
                # sort 已降冪，遇重複保留最新一筆
                if symbol and symbol not in cache:
                    cache[symbol] = doc
            self._db_read_cache = cache
            print(f"📦 預載入 MongoDB 快取：{len(cache)} 筆股票歷史資料")
        except Exception as e:
            print(f"⚠️ MongoDB 預載入失敗，將退回逐筆查詢：{e}")
            self._db_read_cache = {}

    def _flush_db_writes(self):
        """將累積的更新一次性寫回 MongoDB。"""
        if self.db is None or not self._pending_db_writes:
            return
        try:
            result = self.db["twstock"].bulk_write(self._pending_db_writes, ordered=False)
            inserted = result.upserted_count
            modified = result.modified_count
            print(f"💾 批次寫入 MongoDB：新增 {inserted} 筆 / 更新 {modified} 筆")
        except Exception as e:
            print(f"❌ 批次寫入 MongoDB 失敗：{e}")
        finally:
            self._pending_db_writes = []

    def get_stock_price_data(self, stock_code, days=DEFAULT_DAYS):
        """
        獲取股票價格資料 - 智能增量更新

        Args:
            stock_code (str): 股票代碼
            days (int): 取得天數，預設30天

        Returns:
            dict: 包含價格和成交量資料
        """
        try:
            symbol = f"{stock_code}.TW"

            # 先嘗試從預載入的快取讀取
            if self.db is not None:
                stock_data = self._db_read_cache.get(symbol)
                # 若快取沒有 (尚未預載入或新股) 退回原本的 find_one
                if stock_data is None:
                    stock_data = self.db["twstock"].find_one(
                        {"symbol": symbol},
                        sort=[("updated_at", -1)]
                    )

                if stock_data and self._is_data_today(stock_data):
                    price_history = stock_data.get('price_history', {})
                    has_sufficient_data = stock_data.get('has_sufficient_data', False)
                    data_length = stock_data.get('data_length', 0)

                    if price_history and price_history.get('close') and len(price_history.get('close', [])) >= KDJ_MIN_LENGTH:
                        print(f"📖 從資料庫讀取股票 {stock_code} 資料 ({data_length} 天，足夠KDJ: {'是' if has_sufficient_data else '否'})")
                        return price_history
                    else:
                        print(f"⚠️ 資料庫中股票 {stock_code} 資料不足，重新從 API 獲取")

            # 從 twstock 獲取資料
            print(f"🌐 從 API 獲取股票 {stock_code} 價格資料...")
            stock = twstock.Stock(stock_code)
            start_dt = datetime.now() - timedelta(days=FETCH_LOOKBACK_DAYS)
            stock.fetch_from(start_dt.year, start_dt.month)

            if not stock.price:
                return {}

            # 確保取得最近 days 天的資料 (自動維持滑動窗口)
            total_available = len(stock.price)
            actual_days = min(days, total_available)

            recent_data = {
                'dates': stock.date[-actual_days:],
                'open': stock.open[-actual_days:],
                'high': stock.high[-actual_days:],
                'low': stock.low[-actual_days:],
                'close': stock.price[-actual_days:],
                'volume': stock.capacity[-actual_days:],
            }

            print(f"📊 獲取到 {actual_days} 天資料 (要求 {days} 天，可用 {total_available} 天)")

            if self.db is not None:
                self._save_stock_data(stock_code, recent_data)

            return recent_data

        except Exception as e:
            print(f"❌ 獲取股票 {stock_code} 價格資料失敗: {e}")
            return {}

    def _is_data_recent(self, doc_or_date):
        """檢查資料是否為近期資料（使用實際交易日而非執行日）"""
        latest_market_date = _parse_market_date(_extract_latest_market_date(doc_or_date))
        if latest_market_date is None:
            return False

        # 允許跨非交易日的 3 天容忍（遇到週末/連假時仍視為新）
        age_days = (datetime.now().date() - latest_market_date.date()).days
        is_recent = age_days <= DATA_FRESHNESS_DAYS
        try:
            print(f"🗓️ 資料最新交易日: {latest_market_date.date()} | 距今天 {age_days} 天 | 新鮮: {'是' if is_recent else '否'}")
        except Exception:
            pass
        return is_recent

    def _is_data_today(self, doc_or_date):
        """檢查資料的最新交易日是否為今天（價格資料使用）"""
        latest_market_date = _parse_market_date(_extract_latest_market_date(doc_or_date))
        if latest_market_date is None:
            return False

        is_today = latest_market_date.date() == datetime.now().date()
        try:
            print(f"📅 價格資料最新交易日: {latest_market_date.date()} | 必須為今天: {'是' if is_today else '否'}")
        except Exception:
            pass
        return is_today

    def _save_stock_data(self, stock_code, price_data):
        """
        儲存股票資料到資料庫 - 維持30天滑動窗口

        當新增一天資料時，會自動刪除最舊的一天，保持固定的30天資料長度
        實際 write 透過 bulk_write 累積後批次發送，可大幅減少 DB round-trip。
        """
        try:
            if self.db is None:
                return

            # 確保有完整的價格資料
            if not price_data or not price_data.get('close') or len(price_data['close']) == 0:
                print(f"⚠️ 股票 {stock_code} 沒有價格資料，跳過儲存")
                return

            data_length = len(price_data['close'])
            if data_length < KDJ_SUFFICIENT_LENGTH:
                print(f"⚠️ 股票 {stock_code} 資料不足 ({data_length} 天)，但仍儲存")

            latest_idx = -1
            symbol = f"{stock_code}.TW"

            doc = {
                "symbol": symbol,
                "code": stock_code,
                "name": "",
                "date": datetime.now().strftime('%Y-%m-%d'),
                "latest_data": {
                    "date": price_data['dates'][latest_idx] if price_data.get('dates') else None,
                    "open": price_data['open'][latest_idx] if price_data.get('open') else None,
                    "high": price_data['high'][latest_idx] if price_data.get('high') else None,
                    "low": price_data['low'][latest_idx] if price_data.get('low') else None,
                    "close": price_data['close'][latest_idx] if price_data.get('close') else None,
                    "volume": price_data['volume'][latest_idx] if price_data.get('volume') else None,
                },
                "price_history": {
                    "dates": price_data.get('dates', []),
                    "open": price_data.get('open', []),
                    "high": price_data.get('high', []),
                    "low": price_data.get('low', []),
                    "close": price_data.get('close', []),
                    "volume": price_data.get('volume', []),
                },
                "data_length": data_length,
                "updated_at": datetime.now(),
                "has_sufficient_data": data_length >= KDJ_SUFFICIENT_LENGTH,
            }

            # 累積批次更新；本機 cache 也同步更新以保留語意一致
            self._pending_db_writes.append(
                UpdateOne({"symbol": symbol}, {"$set": doc}, upsert=True)
            )
            is_new = symbol not in self._db_read_cache
            self._db_read_cache[symbol] = doc
            if is_new:
                print(f"📝 新增股票 {stock_code} 資料 ({data_length} 天)")
            else:
                print(f"🔄 更新股票 {stock_code} 資料 ({data_length} 天)")

        except Exception as e:
            print(f"❌ 儲存股票 {stock_code} 資料失敗: {e}")

    def check_volume_spike(self, volumes):
        """
        檢查成交量是否異常放大

        Args:
            volumes (list): 成交量序列

        Returns:
            bool: True 如果最新成交量是前一天的 n 倍以上
        """
        if len(volumes) < 2:
            return False

        try:
            latest_volume = volumes[-1]
            previous_volume = volumes[-2]

            if previous_volume == 0:
                return False

            return latest_volume >= previous_volume * self.volume_multiplier
        except (TypeError, IndexError):
            return False

    def analyze_j_value_trend(self, kdj_result):
        """
        分析 J 值趨勢變化 - v2 核心邏輯

        Args:
            kdj_result (dict): KDJ 計算結果，包含 J_series

        Returns:
            dict: 包含趨勢分析結果
        """
        try:
            if 'error' in kdj_result:
                return {"trend": "invalid", "signal": "invalid"}

            kdj_data = kdj_result.get('KDJ', {})
            j_series = kdj_data.get('J_series', [])

            if not j_series:
                return {"trend": "invalid", "signal": "invalid"}

            # 至少需要2天的資料來比較趨勢
            if len(j_series) < 2:
                return {"trend": "insufficient_data", "signal": "invalid"}

            yesterday_j = j_series[-2]
            today_j = j_series[-1]

            result = {
                "trend": "no_signal",
                "signal": "hold",
                "yesterday_j": round(yesterday_j, 2),
                "today_j": round(today_j, 2),
                "change": round(today_j - yesterday_j, 2),
            }

            # v2 判斷邏輯：
            # 買進條件：昨日 J > 10 且 今日 J < 10 (突破下方)
            if yesterday_j > 10 and today_j < 10:
                result["trend"] = "breakthrough_down"
                result["signal"] = "buy"
                print(f"📉 J值向下突破: {yesterday_j:.1f} → {today_j:.1f} (買進信號)")

            # 賣出條件：昨日 J < 90 且 今日 J > 90 (突破上方)
            elif yesterday_j < 90 and today_j > 90:
                result["trend"] = "breakthrough_up"
                result["signal"] = "sell"
                print(f"📈 J值向上突破: {yesterday_j:.1f} → {today_j:.1f} (賣出信號)")

            # 其他情況記錄但不產生信號
            else:
                if today_j < 10:
                    result["trend"] = "oversold"
                elif today_j > 90:
                    result["trend"] = "overbought"
                else:
                    result["trend"] = "normal"

            return result

        except Exception as e:
            print(f"❌ J值趨勢分析失敗: {e}")
            return {"trend": "error", "signal": "invalid"}

    def analyze_single_stock(self, stock_code, pe_data):
        """
        分析單一股票 - v2 版本

        Args:
            stock_code (str): 股票代碼
            pe_data (dict): PE ratio 資料

        Returns:
            dict: 分析結果
        """
        pe_info = pe_data.get(stock_code, {})

        result = {
            'code': stock_code,
            'name': pe_info.get('name', stock_code),
            'signal': 'hold',
            'pe_signal': 'hold',
            'kdj_signal': 'hold',
            'j_trend': {},
            'volume_spike': False,
            'pe_ratio': pe_info.get('pe_ratio'),
            'j_value': None,
            'yesterday_j': None,
            'analysis_time': datetime.now().isoformat(),
        }

        try:
            # 分析 PE ratio
            result['pe_signal'] = self.pe_analyzer.analyze(result['pe_ratio'])

            # 獲取價格資料並分析 KDJ
            price_data = self.get_stock_price_data(stock_code)

            if price_data and price_data.get('high') and len(price_data['high']) >= KDJ_MIN_LENGTH:
                data_length = len(price_data['high'])

                if data_length < KDJ_SUFFICIENT_LENGTH:
                    print(f"⚠️ 股票 {stock_code} 資料不足 ({data_length} 天)，KDJ 計算可能不準確")

                kdj_result = self.kdj_analyzer.get_kdj_signal(
                    price_data['high'],
                    price_data['low'],
                    price_data['close'],
                )

                if 'error' not in kdj_result:
                    result['j_value'] = kdj_result.get('J_value')
                    result['data_length'] = data_length
                    result['kdj_reliable'] = data_length >= KDJ_SUFFICIENT_LENGTH

                    # v2 核心：分析 J 值趨勢變化
                    j_trend_analysis = self.analyze_j_value_trend(kdj_result)
                    result['j_trend'] = j_trend_analysis
                    result['kdj_signal'] = j_trend_analysis['signal']
                    result['yesterday_j'] = j_trend_analysis.get('yesterday_j')

                    # 檢查成交量異常
                    if price_data.get('volume'):
                        result['volume_spike'] = self.check_volume_spike(price_data['volume'])
                else:
                    print(f"❌ 股票 {stock_code} KDJ 計算失敗: {kdj_result.get('error', '未知錯誤')}")
            else:
                print(f"❌ 股票 {stock_code} 資料不足，無法計算 KDJ")

            # 綜合判斷買賣信號 - v2 版本
            if result.get('kdj_reliable', False):
                # v2 買進條件：J值向下突破 且 PE < 20
                if result['kdj_signal'] == 'buy' and result['pe_signal'] == 'buy':
                    result['signal'] = 'buy'
                # v2 賣出條件：J值向上突破 且 PE > 40
                elif result['kdj_signal'] == 'sell' and result['pe_signal'] == 'sell':
                    result['signal'] = 'sell'
            else:
                # 資料不足時，只依賴 PE ratio 給出弱建議
                if result['pe_signal'] == 'buy':
                    result['signal'] = 'weak_buy'
                elif result['pe_signal'] == 'sell':
                    result['signal'] = 'weak_sell'

        except Exception as e:
            print(f"❌ 分析股票 {stock_code} 時發生錯誤: {e}")

        return result

    def run_analysis(self):
        """執行完整的股票分析流程 - v2 版本"""
        start_time = time.time()
        print("🚀 開始執行股票分析 v2...")
        print("🔄 v2 特色: J值趨勢突破判斷")
        print(f"⏰ 開始時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # 檢查是否為交易日
        print("📅 檢查是否為交易日...")
        if not self.is_trading_day():
            print("📅 今日非交易日，跳過分析")
            return
        print("✅ 今日為交易日，繼續執行分析")

        # 獲取股票清單
        print("\n📋 步驟 1/4: 獲取股票清單")
        stock_list = self.get_stock_list()
        if not stock_list:
            print("❌ 無法獲取股票清單，程式結束")
            return

        # 獲取 PE ratio 資料
        print("\n📊 步驟 2/4: 獲取 PE ratio 資料")
        pe_data = self.fetch_pe_data()
        if not pe_data:
            print("❌ 無法獲取 PE ratio 資料，程式結束")
            return

        # 預載入 MongoDB 快取（一次抓取所有股票歷史，避免逐檔 round-trip）
        self._preload_db_cache()

        # 分析每檔股票
        print(f"\n🔍 步驟 3/4: 分析股票 (v2 J值趨勢判斷)")
        buy_recommendations = []
        sell_recommendations = []
        total_analyzed = 0
        error_count = 0

        analyzable_stocks = [code for code in stock_list if code in pe_data]
        print(f"📊 待分析股票: {len(analyzable_stocks)} 檔 (總共 {len(stock_list)} 檔)")

        analysis_start_time = time.time()

        for i, stock_code in enumerate(analyzable_stocks, 1):
            try:
                analysis_result = self.analyze_single_stock(stock_code, pe_data)
                total_analyzed += 1

                signal = analysis_result['signal']
                if signal in ('buy', 'sell'):
                    reliability = "✅可靠" if analysis_result.get('kdj_reliable', False) else "⚠️資料不足"
                    data_info = f"({analysis_result.get('data_length', 0)} 天)"
                    trend_info = f"J: {analysis_result.get('yesterday_j', 'N/A')}→{analysis_result.get('j_value', 'N/A')}"
                    pe_str = analysis_result.get('pe_ratio', 'N/A')
                    if signal == 'buy':
                        buy_recommendations.append(analysis_result)
                        print(f"🔴 買進: {analysis_result['name']} ({stock_code}) - PE: {pe_str}, {trend_info} [{reliability} {data_info}]")
                    else:
                        sell_recommendations.append(analysis_result)
                        print(f"🔵 賣出: {analysis_result['name']} ({stock_code}) - PE: {pe_str}, {trend_info} [{reliability} {data_info}]")
                elif signal in ('weak_buy', 'weak_sell'):
                    signal_type = "買進" if signal == 'weak_buy' else "賣出"
                    emoji = "🟠" if signal == 'weak_buy' else "🟣"
                    print(f"{emoji} 弱{signal_type}: {analysis_result['name']} ({stock_code}) - 僅PE: {analysis_result.get('pe_ratio', 'N/A')} [⚠️KDJ資料不足]")

                if i % PROGRESS_REPORT_INTERVAL == 0:
                    elapsed_time = time.time() - analysis_start_time
                    avg_time_per_stock = elapsed_time / i
                    remaining_stocks = len(analyzable_stocks) - i
                    estimated_remaining_time = remaining_stocks * avg_time_per_stock

                    print(f"📈 分析進度: {i}/{len(analyzable_stocks)} ({i/len(analyzable_stocks)*100:.1f}%)")
                    print(f"⏱️ 已用時間: {elapsed_time:.1f}秒 | 預估剩餘: {estimated_remaining_time:.1f}秒")
                    print(f"📊 目前結果 - 買進: {len(buy_recommendations)} 檔 | 賣出: {len(sell_recommendations)} 檔")

            except Exception as e:
                error_count += 1
                print(f"❌ 分析股票 {stock_code} 時發生錯誤: {e}")
                continue

        # 將累積的更新一次寫回資料庫
        self._flush_db_writes()

        analysis_duration = time.time() - analysis_start_time

        # 發送通知
        print(f"\n📱 步驟 4/4: 發送通知")
        print(f"\n📋 分析完成！(v2 版本)")
        print(f"⏱️ 分析耗時: {analysis_duration:.1f} 秒")
        print(f"📊 總計分析: {total_analyzed} 檔股票")
        print(f"🔴 買進建議: {len(buy_recommendations)} 檔")
        print(f"🔵 賣出建議: {len(sell_recommendations)} 檔")
        print(f"❌ 錯誤數量: {error_count} 檔")

        try:
            print("📱 正在發送 LINE 通知...")

            if self.line_notifier:
                if buy_recommendations or sell_recommendations:
                    all_recommendations = buy_recommendations + sell_recommendations
                    notification_result = self.line_notifier.send_detailed_notification(all_recommendations)
                else:
                    today_date = datetime.now().strftime('%Y-%m-%d')
                    no_signal_message = (
                        f"📊 股票分析完成 v2 ({today_date})\n\n"
                        f"今日無符合條件的買賣建議\n\n"
                        f"分析條件 (v2趨勢突破):\n"
                        f"• 買進: 昨日J>10→今日J<10 且 PE<20\n"
                        f"• 賣出: 昨日J<90→今日J>90 且 PE>40\n"
                        f"• 總計分析: {total_analyzed} 檔股票"
                    )
                    notification_result = self.line_notifier.send_message(no_signal_message)

                if notification_result.get('success'):
                    print("✅ LINE 通知發送成功")
                else:
                    print(f"❌ LINE 通知發送失敗: {notification_result.get('error')}")
            else:
                print("⚠️ LINE 通知器未初始化，跳過通知")

        except Exception as e:
            print(f"❌ 發送通知時發生錯誤: {e}")

        if not buy_recommendations and not sell_recommendations:
            print("📊 今日無符合條件的買賣建議")

        total_time = time.time() - start_time
        print(f"\n🎉 程式執行完成！總耗時: {total_time:.1f} 秒")
        print(f"⏰ 結束時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    def __del__(self):
        """清理資源"""
        if self.mongo_client is not None:
            self.mongo_client.close()


def main():
    """主程式入口"""
    try:
        # 首先檢查今日是否為放假日
        is_holiday, holiday_name, holiday_category = check_if_holiday()

        if is_holiday:
            print(f"📢 今天是{holiday_name} ({holiday_category})，因此沒有開盤，交易暫停一日")

            try:
                line_notifier = LineNotifier()
                message = f"📅 台股休市通知\n\n今天是{holiday_name} ({holiday_category})，因此沒有開盤，交易暫停一日。"
                result = line_notifier.send_message(message)

                if result.get('success'):
                    print("✅ LINE 通知發送成功")
                else:
                    print(f"⚠️ LINE 通知發送失敗: {result.get('error')}")
            except Exception as e:
                print(f"⚠️ 無法發送 LINE 通知: {e}")

            print("🛑 程式結束")
            return

        system = StockAnalysisSystemV2()
        system.run_analysis()
    except KeyboardInterrupt:
        print("\n⚠️ 程式被使用者中斷")
    except Exception as e:
        print(f"❌ 程式執行錯誤: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
