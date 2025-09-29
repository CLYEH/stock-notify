# 台股分析通知系統

這是一個自動化的台股分析系統，結合 PE ratio 和 KDJ 技術指標分析，透過 LINE Official API 發送買賣建議通知。

## 功能特色

- **PE Ratio 分析**: 分析股票本益比，識別低估或高估的股票
- **KDJ 技術指標**: 計算 KDJ 指標，判斷超買或超賣狀態  
- **成交量分析**: 檢測成交量異常放大的股票
- **LINE 通知**: 自動發送分析結果到 LINE
- **MongoDB 整合**: 儲存歷史資料，提升分析效率

## 買賣信號條件

### 買進條件
- J 值 < 10 (超賣)
- PE ratio < 20 (低估)

### 賣出條件  
- J 值 > 90 (超買)
- PE ratio > 40 (高估)

### 成交量標記
當符合買賣條件的股票，其當日成交量達到前一交易日的 2 倍以上時，會以星號 (*) 標記。

## 安裝與設定

### 1. 安裝依賴套件

```bash
pip install -r requirements.txt
```

### 2. 環境變數設定

複製 `.env.example` 為 `.env` 並填入相關設定：

```bash
cp .env.example .env
```

編輯 `.env` 檔案：

```env
# LINE Bot 設定
LINE_TOKEN=your_line_channel_access_token_here
LINE_USER_ID=your_line_user_id_here

# MongoDB 設定  
MONGO_PASSWORD=your_mongodb_password_here

# 成交量倍數設定 (可選，預設為 2.0)
VOLUME_MULTIPLIER=2.0
```

### 3. LINE Bot 設定

1. 前往 [LINE Developers Console](https://developers.line.biz/)
2. 建立新的 Channel (Messaging API)
3. 取得 Channel Access Token
4. 取得要接收通知的 User ID

### 4. MongoDB 設定

1. 建立 MongoDB 資料庫 (可使用 MongoDB Atlas)
2. 取得連接密碼
3. 更新 `main.py` 中的連接字串 (如需要)

## 使用方法

### 執行分析

```bash
python main.py
```

### 定時執行

建議設定為每日收盤後自動執行，可使用 cron job：

```bash
# 每日下午 2:30 執行 (收盤後)
30 14 * * 1-5 cd /path/to/stock && python main.py
```

## 專案結構

```
stock/
├── main.py                 # 主程式
├── src/
│   ├── notify.py           # LINE 通知模組
│   └── stock_tool/
│       ├── pe.py          # PE ratio 分析
│       └── kdj.py         # KDJ 技術指標分析
├── sample_data/
│   └── BWIBBU_ALL.json    # 範例資料
├── requirements.txt        # 依賴套件
├── .env.example           # 環境變數範例
└── README.md              # 說明文件
```

## 核心模組說明

### PE Ratio 分析器 (`src/stock_tool/pe.py`)

- `PERatioAnalyzer`: 分析股票本益比
- 買進閾值: PE < 20
- 賣出閾值: PE > 40

### KDJ 分析器 (`src/stock_tool/kdj.py`)

- `KDJAnalyzer`: 計算 KDJ 技術指標
- 買進閾值: J < 10 (超賣)
- 賣出閾值: J > 90 (超買)

### LINE 通知器 (`src/notify.py`)

- `LineNotifier`: 發送 LINE 通知
- 支援簡單和詳細通知格式
- 自動格式化買賣建議訊息

## 通知範例

```
🔴 買進建議
台積電 2330 * (PE: 18.5, J: 8.2)
緯創 3231 (PE: 15.3, J: 9.1)

🔵 賣出建議  
鴻海 2317 (PE: 42.1, J: 91.5)

* 表示成交量異常放大
```

## 注意事項

1. **交易日檢查**: 程式會自動檢查是否為交易日
2. **資料來源**: PE ratio 資料來自證交所公開資訊
3. **歷史資料**: KDJ 計算需要至少 9 個交易日的資料
4. **網路連線**: 需要穩定的網路連線以獲取即時資料
5. **API 限制**: 注意證交所 API 的使用限制

## 故障排除

### 常見問題

1. **MongoDB 連接失敗**
   - 檢查 `MONGO_PASSWORD` 是否正確
   - 確認網路連線正常

2. **LINE 通知失敗**
   - 檢查 `LINE_TOKEN` 和 `LINE_USER_ID` 是否正確
   - 確認 LINE Bot 已加為好友

3. **股票資料獲取失敗**
   - 檢查網路連線
   - 證交所 API 可能暫時無法使用

## 授權

此專案僅供學習和個人使用，請勿用於商業用途。使用時請遵守相關法規和 API 使用條款。
