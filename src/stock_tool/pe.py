"""
PE Ratio 分析模組
用於分析股票的本益比，判斷買進或賣出時機
"""

class PERatioAnalyzer:
    def __init__(self):
        self.buy_threshold = 20  # PE ratio 小於 20 考慮買進
        self.sell_threshold = 40  # PE ratio 大於 40 考慮賣出
    
    def analyze(self, pe_ratio):
        """
        分析 PE ratio 並返回建議
        
        Args:
            pe_ratio (float or str): PE ratio 值
            
        Returns:
            str: 'buy', 'sell', 'hold', 或 'invalid'
        """
        try:
            # 處理空值或無效值
            if pe_ratio is None or pe_ratio == "" or pe_ratio == "-":
                return "invalid"
            
            # 轉換為浮點數
            pe_value = float(pe_ratio)
            
            # 判斷買賣時機
            if pe_value < self.buy_threshold:
                return "buy"
            elif pe_value > self.sell_threshold:
                return "sell"
            else:
                return "hold"
                
        except (ValueError, TypeError):
            return "invalid"
    
    def is_valid_pe(self, pe_ratio):
        """
        檢查 PE ratio 是否為有效值
        
        Args:
            pe_ratio (float or str): PE ratio 值
            
        Returns:
            bool: True 如果有效，False 如果無效
        """
        try:
            if pe_ratio is None or pe_ratio == "" or pe_ratio == "-":
                return False
            float(pe_ratio)
            return True
        except (ValueError, TypeError):
            return False
    
    def get_pe_signal(self, pe_ratio):
        """
        獲取 PE ratio 信號強度
        
        Args:
            pe_ratio (float or str): PE ratio 值
            
        Returns:
            dict: 包含信號類型和強度的字典
        """
        analysis = self.analyze(pe_ratio)
        
        if analysis == "invalid":
            return {"signal": "invalid", "strength": 0}
        
        try:
            pe_value = float(pe_ratio)
            
            if analysis == "buy":
                # PE 越低，買進信號越強
                strength = max(0, min(1, (self.buy_threshold - pe_value) / self.buy_threshold))
                return {"signal": "buy", "strength": strength, "pe_value": pe_value}
            elif analysis == "sell":
                # PE 越高，賣出信號越強
                strength = min(1, (pe_value - self.sell_threshold) / self.sell_threshold)
                return {"signal": "sell", "strength": strength, "pe_value": pe_value}
            else:
                return {"signal": "hold", "strength": 0, "pe_value": pe_value}
                
        except (ValueError, TypeError):
            return {"signal": "invalid", "strength": 0}
