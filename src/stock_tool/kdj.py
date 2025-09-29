"""
KDJ 技術指標分析模組
用於計算和分析股票的 KDJ 指標，判斷買進或賣出時機
"""

import pandas as pd
import numpy as np

class KDJAnalyzer:
    def __init__(self, k_period=9, d_period=3, j_period=3):
        """
        初始化 KDJ 分析器
        
        Args:
            k_period (int): K 值計算週期，預設 9
            d_period (int): D 值計算週期，預設 3  
            j_period (int): J 值計算週期，預設 3
        """
        self.k_period = k_period
        self.d_period = d_period
        self.j_period = j_period
        
        # 買賣信號閾值
        self.buy_threshold = 10   # J 值小於 10 考慮買進
        self.sell_threshold = 90  # J 值大於 90 考慮賣出
    
    def calculate_kdj(self, high_prices, low_prices, close_prices):
        """
        計算 KDJ 指標
        
        Args:
            high_prices (list): 最高價序列
            low_prices (list): 最低價序列  
            close_prices (list): 收盤價序列
            
        Returns:
            dict: 包含 K, D, J 值的字典
        """
        if len(high_prices) < self.k_period:
            return {"K": None, "D": None, "J": None, "error": "資料不足，無法計算 KDJ"}
        
        try:
            # 轉換為 pandas Series
            high = pd.Series(high_prices)
            low = pd.Series(low_prices)
            close = pd.Series(close_prices)
            
            # 計算 RSV (Raw Stochastic Value)
            lowest_low = low.rolling(window=self.k_period).min()
            highest_high = high.rolling(window=self.k_period).max()
            
            rsv = (close - lowest_low) / (highest_high - lowest_low) * 100
            rsv = rsv.fillna(50)  # 第一個值設為 50
            
            # 計算 K 值 (使用指數移動平均)
            k_values = []
            k = 50  # 初始值
            
            for rsv_val in rsv:
                if pd.isna(rsv_val):
                    k_values.append(k)
                else:
                    k = (2/3) * k + (1/3) * rsv_val
                    k_values.append(k)
            
            k_series = pd.Series(k_values)
            
            # 計算 D 值 (K 值的指數移動平均)
            d_values = []
            d = 50  # 初始值
            
            for k_val in k_series:
                d = (2/3) * d + (1/3) * k_val
                d_values.append(d)
            
            d_series = pd.Series(d_values)
            
            # 計算 J 值
            j_series = 3 * k_series - 2 * d_series
            
            # 返回最新的 KDJ 值
            return {
                "K": round(k_series.iloc[-1], 2),
                "D": round(d_series.iloc[-1], 2), 
                "J": round(j_series.iloc[-1], 2),
                "K_series": k_series.tolist(),
                "D_series": d_series.tolist(),
                "J_series": j_series.tolist()
            }
            
        except Exception as e:
            return {"K": None, "D": None, "J": None, "error": f"計算 KDJ 時發生錯誤: {str(e)}"}
    
    def analyze_j_value(self, j_value):
        """
        分析 J 值並返回買賣建議
        
        Args:
            j_value (float): J 值
            
        Returns:
            str: 'buy', 'sell', 'hold', 或 'invalid'
        """
        if j_value is None:
            return "invalid"
        
        try:
            j = float(j_value)
            
            if j < self.buy_threshold:
                return "buy"
            elif j > self.sell_threshold:
                return "sell"
            else:
                return "hold"
                
        except (ValueError, TypeError):
            return "invalid"
    
    def get_kdj_signal(self, high_prices, low_prices, close_prices):
        """
        獲取 KDJ 信號
        
        Args:
            high_prices (list): 最高價序列
            low_prices (list): 最低價序列
            close_prices (list): 收盤價序列
            
        Returns:
            dict: 包含信號類型、強度和 KDJ 值的字典
        """
        kdj_result = self.calculate_kdj(high_prices, low_prices, close_prices)
        
        if "error" in kdj_result:
            return {"signal": "invalid", "strength": 0, "error": kdj_result["error"]}
        
        j_value = kdj_result["J"]
        analysis = self.analyze_j_value(j_value)
        
        if analysis == "invalid":
            return {"signal": "invalid", "strength": 0}
        
        try:
            j = float(j_value)
            
            if analysis == "buy":
                # J 值越低，買進信號越強
                strength = max(0, min(1, (self.buy_threshold - j) / self.buy_threshold))
                return {
                    "signal": "buy", 
                    "strength": strength, 
                    "KDJ": kdj_result,
                    "J_value": j
                }
            elif analysis == "sell":
                # J 值越高，賣出信號越強  
                strength = min(1, (j - self.sell_threshold) / (100 - self.sell_threshold))
                return {
                    "signal": "sell", 
                    "strength": strength, 
                    "KDJ": kdj_result,
                    "J_value": j
                }
            else:
                return {
                    "signal": "hold", 
                    "strength": 0, 
                    "KDJ": kdj_result,
                    "J_value": j
                }
                
        except (ValueError, TypeError):
            return {"signal": "invalid", "strength": 0}
    
    def is_oversold(self, j_value):
        """
        判斷是否超賣 (J < 10)
        
        Args:
            j_value (float): J 值
            
        Returns:
            bool: True 如果超賣
        """
        try:
            return float(j_value) < self.buy_threshold
        except (ValueError, TypeError):
            return False
    
    def is_overbought(self, j_value):
        """
        判斷是否超買 (J > 90)
        
        Args:
            j_value (float): J 值
            
        Returns:
            bool: True 如果超買
        """
        try:
            return float(j_value) > self.sell_threshold
        except (ValueError, TypeError):
            return False
