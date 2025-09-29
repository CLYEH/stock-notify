"""
LINE 通知模組
用於透過 LINE Official API 發送股票分析通知
"""

import requests
import json
import os
from typing import List, Dict

class LineNotifier:
    def __init__(self, line_token=None, line_user_id=None):
        """
        初始化 LINE 通知器
        
        Args:
            line_token (str): LINE Bot 的 Channel Access Token
            line_user_id (str): 要發送訊息的使用者 ID
        """
        self.line_token = line_token or os.getenv('LINE_TOKEN')
        self.line_user_id = line_user_id or os.getenv('LINE_USER_ID')
        self.api_url = "https://api.line.me/v2/bot/message/push"
        
        if not self.line_token:
            raise ValueError("LINE_TOKEN 未設定，請在環境變數中設定")
        if not self.line_user_id:
            raise ValueError("LINE_USER_ID 未設定，請在環境變數中設定")
    
    def send_message(self, message):
        """
        發送文字訊息到 LINE
        
        Args:
            message (str): 要發送的訊息內容
            
        Returns:
            dict: API 回應結果
        """
        headers = {
            'Authorization': f'Bearer {self.line_token}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'to': self.line_user_id,
            'messages': [
                {
                    'type': 'text',
                    'text': message
                }
            ]
        }
        
        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                data=json.dumps(data),
                timeout=10
            )
            
            if response.status_code == 200:
                return {"success": True, "message": "訊息發送成功"}
            else:
                return {
                    "success": False, 
                    "error": f"發送失敗，狀態碼: {response.status_code}",
                    "response": response.text
                }
                
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"網路錯誤: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"未知錯誤: {str(e)}"}
    
    def format_stock_notification(self, buy_stocks, sell_stocks):
        """
        格式化股票通知訊息
        
        Args:
            buy_stocks (list): 買進股票列表，每個元素包含 {'code', 'name', 'volume_spike'}
            sell_stocks (list): 賣出股票列表，每個元素包含 {'code', 'name', 'volume_spike'}
            
        Returns:
            str: 格式化後的訊息
        """
        message_parts = []
        
        # 買進通知
        if buy_stocks:
            message_parts.append("🔴 買進")
            for stock in buy_stocks:
                stock_line = f"{stock['name']} {stock['code']}"
                if stock.get('volume_spike', False):
                    stock_line += " *"
                message_parts.append(stock_line)
            message_parts.append("")  # 空行
        
        # 賣出通知
        if sell_stocks:
            message_parts.append("🔵 賣出")
            for stock in sell_stocks:
                stock_line = f"{stock['name']} {stock['code']}"
                if stock.get('volume_spike', False):
                    stock_line += " *"
                message_parts.append(stock_line)
        
        if not buy_stocks and not sell_stocks:
            return "今日無買賣建議"
        
        return "\n".join(message_parts)
    
    def send_stock_notification(self, buy_stocks, sell_stocks):
        """
        發送股票分析通知
        
        Args:
            buy_stocks (list): 買進股票列表
            sell_stocks (list): 賣出股票列表
            
        Returns:
            dict: 發送結果
        """
        message = self.format_stock_notification(buy_stocks, sell_stocks)
        return self.send_message(message)
    
    def format_detailed_notification(self, stock_analyses):
        """
        格式化詳細的股票分析通知
        
        Args:
            stock_analyses (list): 股票分析結果列表
            
        Returns:
            str: 格式化後的訊息
        """
        buy_stocks = []
        sell_stocks = []
        
        for analysis in stock_analyses:
            stock_info = {
                'code': analysis['code'],
                'name': analysis['name'],
                'volume_spike': analysis.get('volume_spike', False),
                'pe_ratio': analysis.get('pe_ratio'),
                'j_value': analysis.get('j_value')
            }
            
            if analysis['signal'] == 'buy':
                buy_stocks.append(stock_info)
            elif analysis['signal'] == 'sell':
                sell_stocks.append(stock_info)
        
        message_parts = []
        
        # 買進通知（詳細版）
        if buy_stocks:
            message_parts.append("🔴 買進建議")
            for stock in buy_stocks:
                stock_line = f"{stock['name']} {stock['code']}"
                if stock.get('volume_spike', False):
                    stock_line += " *"
                
                details = []
                if stock.get('pe_ratio') is not None:
                    details.append(f"PE: {stock['pe_ratio']}")
                if stock.get('j_value') is not None:
                    details.append(f"J: {stock['j_value']:.1f}")
                
                if details:
                    stock_line += f" ({', '.join(details)})"
                
                message_parts.append(stock_line)
            message_parts.append("")  # 空行
        
        # 賣出通知（詳細版）
        if sell_stocks:
            message_parts.append("🔵 賣出建議")
            for stock in sell_stocks:
                stock_line = f"{stock['name']} {stock['code']}"
                if stock.get('volume_spike', False):
                    stock_line += " *"
                
                details = []
                if stock.get('pe_ratio') is not None:
                    details.append(f"PE: {stock['pe_ratio']}")
                if stock.get('j_value') is not None:
                    details.append(f"J: {stock['j_value']:.1f}")
                
                if details:
                    stock_line += f" ({', '.join(details)})"
                
                message_parts.append(stock_line)
        
        if not buy_stocks and not sell_stocks:
            return "📊 今日股票分析完成\n無符合條件的買賣建議"
        
        # 添加說明
        if any(stock.get('volume_spike') for stock in buy_stocks + sell_stocks):
            message_parts.append("")
            message_parts.append("* 表示成交量異常放大")
        
        return "\n".join(message_parts)
    
    def send_detailed_notification(self, stock_analyses):
        """
        發送詳細的股票分析通知
        
        Args:
            stock_analyses (list): 股票分析結果列表
            
        Returns:
            dict: 發送結果
        """
        message = self.format_detailed_notification(stock_analyses)
        return self.send_message(message)
    
    def test_connection(self):
        """
        測試 LINE API 連接
        
        Returns:
            dict: 測試結果
        """
        test_message = "📈 股票分析系統測試訊息"
        return self.send_message(test_message)
