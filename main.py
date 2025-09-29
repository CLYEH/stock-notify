"""
台股分析通知系統主程式
整合 PE ratio、KDJ 技術指標分析，並透過 LINE 發送通知
"""

import os
import sys
import json
import requests
import twstock
import pandas as pd
import time
from datetime import datetime, timedelta
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

# 加入 src 路徑
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from stock_tool.pe import PERatioAnalyzer
from stock_tool.kdj import KDJAnalyzer
from notify import LineNotifier


class StockAnalysisSystem:
    def __init__(self):
        """初始化股票分析系統"""
        print("🔧 正在初始化系統...")
        
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
        
        # 資料 API URL
        self.pe_data_url = 'https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL'
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
            
            # 根據您的成功範例建立 MongoDB 連接字串
            mongo_uri = f"mongodb+srv://leoyeh906_db_user:{mongo_password}@cluster0.zwdnfad.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
            
            # 建立連接
            self.mongo_client = MongoClient(mongo_uri, server_api=ServerApi('1'))
            
            # 測試連接
            self.mongo_client.admin.command('ping')
            
            # 設定資料庫和集合
            self.db = self.mongo_client['StockStrategy']
            
            print("✅ MongoDB 連接成功")
            
        except Exception as e:
            print(f"❌ MongoDB 連接失敗: {e}")
            # 可以選擇繼續執行但不使用資料庫功能
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
        weekday = date.weekday()
        return weekday < 5  # 0-4 代表週一到週五
    
    def get_stock_list(self):
        """
        獲取要分析的股票清單
        
        Returns:
            list: 股票代碼清單
        """
        try:
            # 獲取所有上市股票
            stock_list = [code for code in twstock.twse if twstock.twse[code].type == '股票']
            print(f"✅ 取得股票清單，共 {len(stock_list)} 檔股票")
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
            print(f"📡 發送請求到: {self.pe_data_url}")
            response = requests.get(self.pe_data_url, timeout=30)
            response.raise_for_status()
            print("✅ API 請求成功，正在解析資料...")
            
            pe_data = {}
            data = response.json()
            print(f"📋 收到 {len(data)} 筆原始資料，正在處理...")
            
            valid_count = 0
            for item in data:
                code = item.get('Code')
                pe_ratio = item.get('PEratio')
                name = item.get('Name')
                
                if code and pe_ratio:
                    pe_data[code] = {
                        'pe_ratio': pe_ratio,
                        'name': name
                    }
                    valid_count += 1
            
            print(f"✅ 取得 PE ratio 資料，共 {len(pe_data)} 檔股票 (有效資料: {valid_count} 筆)")
            return pe_data
            
        except Exception as e:
            print(f"❌ 獲取 PE ratio 資料失敗: {e}")
            return {}
    
    def get_stock_price_data(self, stock_code, days=30):
        """
        獲取股票價格資料 - 智能增量更新
        
        Args:
            stock_code (str): 股票代碼
            days (int): 取得天數，預設30天
            
        Returns:
            dict: 包含價格和成交量資料
        """
        try:
            # 先嘗試從資料庫獲取
            if self.db is not None:
                collection = self.db["twstock"]
                stock_data = collection.find_one(
                    {"symbol": f"{stock_code}.TW"},
                    sort=[("updated_at", -1)]
                )
                
                if stock_data and self._is_data_recent(stock_data.get('date')):
                    # 檢查是否有足夠的歷史資料
                    price_history = stock_data.get('price_history', {})
                    has_sufficient_data = stock_data.get('has_sufficient_data', False)
                    data_length = stock_data.get('data_length', 0)
                    
                    if price_history and price_history.get('close') and len(price_history.get('close', [])) >= 9:
                        print(f"📖 從資料庫讀取股票 {stock_code} 資料 ({data_length} 天，足夠KDJ: {'是' if has_sufficient_data else '否'})")
                        return price_history
                    else:
                        print(f"⚠️ 資料庫中股票 {stock_code} 資料不足，重新從 API 獲取")
            
            # 從 twstock 獲取資料
            print(f"🌐 從 API 獲取股票 {stock_code} 價格資料...")
            stock = twstock.Stock(stock_code)
            stock.fetch_from(2024, 1)  # 從 2024 年開始取得資料
            
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
                'volume': stock.capacity[-actual_days:]
            }
            
            print(f"📊 獲取到 {actual_days} 天資料 (要求 {days} 天，可用 {total_available} 天)")
            
            # 儲存到資料庫 (會自動維持30天滑動窗口)
            if self.db is not None:
                self._save_stock_data(stock_code, recent_data)
            
            return recent_data
            
        except Exception as e:
            print(f"❌ 獲取股票 {stock_code} 價格資料失敗: {e}")
            return {}
    
    def _is_data_recent(self, date):
        """檢查資料是否為近期資料"""
        if not date:
            return False
        
        if isinstance(date, str):
            try:
                date = datetime.strptime(date, '%Y-%m-%d')
            except:
                return False
        
        return (datetime.now() - date).days <= 1
    
    def _save_stock_data(self, stock_code, price_data):
        """
        儲存股票資料到資料庫 - 維持30天滑動窗口
        
        當新增一天資料時，會自動刪除最舊的一天，保持固定的30天資料長度
        """
        try:
            if self.db is None:
                return
            
            # 使用 twstock 集合，符合您的資料庫結構
            collection = self.db["twstock"]
            
            # 確保有完整的價格資料
            if not price_data or not price_data.get('close') or len(price_data['close']) == 0:
                print(f"⚠️ 股票 {stock_code} 沒有價格資料，跳過儲存")
                return
            
            # 檢查資料是否足夠 (至少需要30天用於KDJ計算)
            data_length = len(price_data['close'])
            if data_length < 30:
                print(f"⚠️ 股票 {stock_code} 資料不足 ({data_length} 天)，但仍儲存")
            
            latest_idx = -1  # 最新的資料
            
            doc = {
                "symbol": f"{stock_code}.TW",
                "code": stock_code,
                "name": "",  # 會在後續更新
                "date": datetime.now().strftime('%Y-%m-%d'),
                # 最新一天的資料 (用於快速查詢)
                "latest_data": {
                    "date": price_data['dates'][latest_idx] if price_data.get('dates') and len(price_data['dates']) > 0 else None,
                    "open": price_data['open'][latest_idx] if price_data.get('open') else None,
                    "high": price_data['high'][latest_idx] if price_data.get('high') else None,
                    "low": price_data['low'][latest_idx] if price_data.get('low') else None,
                    "close": price_data['close'][latest_idx] if price_data.get('close') else None,
                    "volume": price_data['volume'][latest_idx] if price_data.get('volume') else None,
                },
                # 完整的歷史資料 (用於KDJ計算)
                "price_history": {
                    "dates": price_data.get('dates', []),
                    "open": price_data.get('open', []),
                    "high": price_data.get('high', []),
                    "low": price_data.get('low', []),
                    "close": price_data.get('close', []),
                    "volume": price_data.get('volume', [])
                },
                "data_length": data_length,
                "updated_at": datetime.now(),
                "has_sufficient_data": data_length >= 30  # 標記是否有足夠資料計算KDJ
            }
            
            # 使用 upsert 更新或插入
            result = collection.update_one(
                {"symbol": f"{stock_code}.TW"},
                {"$set": doc},
                upsert=True
            )
            
            if result.upserted_id:
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
        except:
            return False
    
    def analyze_single_stock(self, stock_code, pe_data):
        """
        分析單一股票
        
        Args:
            stock_code (str): 股票代碼
            pe_data (dict): PE ratio 資料
            
        Returns:
            dict: 分析結果
        """
        result = {
            'code': stock_code,
            'name': pe_data.get(stock_code, {}).get('name', ''),
            'signal': 'hold',
            'pe_signal': 'hold',
            'kdj_signal': 'hold',
            'volume_spike': False,
            'pe_ratio': None,
            'j_value': None,
            'analysis_time': datetime.now().isoformat()
        }
        
        try:
            # 獲取 PE ratio
            pe_info = pe_data.get(stock_code, {})
            pe_ratio = pe_info.get('pe_ratio')
            result['name'] = pe_info.get('name', stock_code)
            result['pe_ratio'] = pe_ratio
            
            # 分析 PE ratio
            pe_analysis = self.pe_analyzer.analyze(pe_ratio)
            result['pe_signal'] = pe_analysis
            
            # 獲取價格資料並分析 KDJ
            price_data = self.get_stock_price_data(stock_code)
            
            if price_data and price_data.get('high') and len(price_data['high']) >= 9:
                data_length = len(price_data['high'])
                
                # 檢查資料是否足夠進行可靠的 KDJ 分析
                if data_length < 30:
                    print(f"⚠️ 股票 {stock_code} 資料不足 ({data_length} 天)，KDJ 計算可能不準確")
                
                kdj_result = self.kdj_analyzer.get_kdj_signal(
                    price_data['high'],
                    price_data['low'], 
                    price_data['close']
                )
                
                if 'error' not in kdj_result:
                    result['kdj_signal'] = kdj_result['signal']
                    result['j_value'] = kdj_result.get('J_value')
                    result['data_length'] = data_length
                    result['kdj_reliable'] = data_length >= 30  # 標記 KDJ 是否可靠
                    
                    # 檢查成交量異常
                    if price_data.get('volume'):
                        result['volume_spike'] = self.check_volume_spike(price_data['volume'])
                else:
                    print(f"❌ 股票 {stock_code} KDJ 計算失敗: {kdj_result.get('error', '未知錯誤')}")
            else:
                print(f"❌ 股票 {stock_code} 資料不足，無法計算 KDJ")
            
            # 綜合判斷買賣信號
            # 只有在 KDJ 資料可靠的情況下才給出建議
            if result.get('kdj_reliable', False):
                # 買進條件：J < 10 且 PE < 20
                if (result['kdj_signal'] == 'buy' and result['pe_signal'] == 'buy'):
                    result['signal'] = 'buy'
                # 賣出條件：J > 90 且 PE > 40  
                elif (result['kdj_signal'] == 'sell' and result['pe_signal'] == 'sell'):
                    result['signal'] = 'sell'
            else:
                # 資料不足時，只依賴 PE ratio 給出弱建議
                if result['pe_signal'] == 'buy':
                    result['signal'] = 'weak_buy'  # 弱買進信號
                elif result['pe_signal'] == 'sell':
                    result['signal'] = 'weak_sell'  # 弱賣出信號
            
        except Exception as e:
            print(f"❌ 分析股票 {stock_code} 時發生錯誤: {e}")
        
        return result
    
    def run_analysis(self):
        """執行完整的股票分析流程"""
        start_time = time.time()
        print("🚀 開始執行股票分析...")
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
        
        # 分析每檔股票
        print(f"\n🔍 步驟 3/4: 分析股票")
        buy_recommendations = []
        sell_recommendations = []
        total_analyzed = 0
        skipped_count = 0
        error_count = 0
        
        # 計算有 PE 資料的股票數量
        analyzable_stocks = [code for code in stock_list if code in pe_data]
        print(f"📊 待分析股票: {len(analyzable_stocks)} 檔 (總共 {len(stock_list)} 檔)")
        
        analysis_start_time = time.time()
        
        for i, stock_code in enumerate(analyzable_stocks, 1):
            try:
                # 顯示當前分析的股票
                stock_name = pe_data[stock_code].get('name', stock_code)
                
                analysis_result = self.analyze_single_stock(stock_code, pe_data)
                total_analyzed += 1
                
                if analysis_result['signal'] == 'buy':
                    buy_recommendations.append(analysis_result)
                    reliability = "✅可靠" if analysis_result.get('kdj_reliable', False) else "⚠️資料不足"
                    data_info = f"({analysis_result.get('data_length', 0)} 天)"
                    print(f"🔴 買進: {analysis_result['name']} ({stock_code}) - PE: {analysis_result.get('pe_ratio', 'N/A')}, J: {analysis_result.get('j_value', 'N/A')} [{reliability} {data_info}]")
                elif analysis_result['signal'] == 'sell':
                    sell_recommendations.append(analysis_result)
                    reliability = "✅可靠" if analysis_result.get('kdj_reliable', False) else "⚠️資料不足"
                    data_info = f"({analysis_result.get('data_length', 0)} 天)"
                    print(f"🔵 賣出: {analysis_result['name']} ({stock_code}) - PE: {analysis_result.get('pe_ratio', 'N/A')}, J: {analysis_result.get('j_value', 'N/A')} [{reliability} {data_info}]")
                elif analysis_result['signal'] in ['weak_buy', 'weak_sell']:
                    signal_type = "買進" if analysis_result['signal'] == 'weak_buy' else "賣出"
                    emoji = "🟠" if analysis_result['signal'] == 'weak_buy' else "🟣"
                    print(f"{emoji} 弱{signal_type}: {analysis_result['name']} ({stock_code}) - 僅PE: {analysis_result.get('pe_ratio', 'N/A')} [⚠️KDJ資料不足]")
                
                # 每分析 25 檔股票顯示進度
                if i % 25 == 0:
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
        
        analysis_end_time = time.time()
        analysis_duration = analysis_end_time - analysis_start_time
        
        # 發送通知
        print(f"\n📱 步驟 4/4: 發送通知")
        print(f"\n📋 分析完成！")
        print(f"⏱️ 分析耗時: {analysis_duration:.1f} 秒")
        print(f"📊 總計分析: {total_analyzed} 檔股票")
        print(f"🔴 買進建議: {len(buy_recommendations)} 檔")
        print(f"🔵 賣出建議: {len(sell_recommendations)} 檔")
        print(f"❌ 錯誤數量: {error_count} 檔")
        
        if buy_recommendations or sell_recommendations:
            try:
                print("📱 正在發送 LINE 通知...")
                
                if self.line_notifier:
                    # 準備通知資料
                    all_recommendations = buy_recommendations + sell_recommendations
                    
                    # 發送 LINE 通知
                    notification_result = self.line_notifier.send_detailed_notification(all_recommendations)
                    
                    if notification_result.get('success'):
                        print("✅ LINE 通知發送成功")
                    else:
                        print(f"❌ LINE 通知發送失敗: {notification_result.get('error')}")
                else:
                    print("⚠️ LINE 通知器未初始化，跳過通知")
                    
            except Exception as e:
                print(f"❌ 發送通知時發生錯誤: {e}")
        else:
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
        system = StockAnalysisSystem()
        system.run_analysis()
    except KeyboardInterrupt:
        print("\n⚠️ 程式被使用者中斷")
    except Exception as e:
        print(f"❌ 程式執行錯誤: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
