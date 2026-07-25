# 台股分析通知系統

每個交易日收盤後掃描**上市與上櫃**全市場，結合本益比與 KDJ 指標判斷買賣時機，透過 LINE 發送通知。

興櫃未納入：興櫃採議價交易制，端點只提供買賣報價與前一日均價，沒有開高低收，KDJ 的 RSV 無從計算。要納入需要另一套訊號規則，不是接資料源就能解決。

## 買賣訊號條件

判定的是 **J 值的跨日穿越**，不是所在區間 —— 長期低於 10 的股票不會每天重複觸發，只有跌破當天算一次訊號。

| | 條件 |
|---|---|
| **買進** | 昨日 J > 10 **且** 今日 J < 10，**並且** PE < 20 |
| **賣出** | 昨日 J < 90 **且** 今日 J > 90，**並且** PE > 40 |

兩個條件必須同時成立。KDJ 資料不足 30 個交易日時一律不產生訊號。

符合條件的股票若當日成交量達前一交易日的 `VOLUME_MULTIPLIER` 倍（預設 2 倍）以上，會以星號 `*` 標記。

## 資料來源與頻率限制

證交所對 `www.twse.com.tw` 有約 **6 秒一次**的請求頻率限制，超過會鎖 IP 一小時。因此本系統一律使用**全市場端點**，一個請求涵蓋所有個股：

| 市場 | 用途 | 端點 | 請求數 |
|---|---|---|---|
| 上市 | 本益比 / 分析標的清單 | `openapi.twse` BWIBBU_ALL | 1 |
| 上市 | 最新交易日行情 | `openapi.twse` STOCK_DAY_ALL | 1 |
| 上市 | 歷史回補 | `www.twse` MI_INDEX | 每個缺漏交易日 1 個 |
| 上櫃 | 本益比 / 分析標的清單 | `openapi.tpex` peratio_analysis | 1 |
| 上櫃 | 最新交易日行情 | `openapi.tpex` daily_close_quotes | 1 |
| 上櫃 | 歷史回補 | `www.tpex` afterTrading/otc | 每個缺漏交易日 1 個 |
| — | 交易日行事曆 | 新北市開放資料 | 1 |

**每日執行約 5 個請求。** 歷史回補只在資料庫缺資料時發生，兩個市場的限流額度獨立（不同主機），各自以 6 秒間隔節流。

行事曆用於推算過去哪些日子有開盤，避免對週末假日發出無謂的請求。

新增市場只需在 `sources/market.py` 的 `MARKETS` 註冊，pipeline 不需改動。

## 安裝與設定

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"      # 開發用；僅執行則 pip install -e .
cp .env.example .env
```

### 環境變數

| 變數 | 必要 | 說明 |
|---|---|---|
| `LINE_TOKEN` | 否 | LINE Channel Access Token。未設定時訊息只寫入 log，不實際發送 |
| `LINE_USER_ID` | 否 | 收件者 ID。前綴 `U`=個人、`C`=群組、`R`=聊天室 |
| `MONGO_URI` | 否 | 完整連線字串。未設定時仍可執行，但每次都要付冷啟動成本 |
| `VOLUME_MULTIPLIER` | 否 | 成交量放大倍數，預設 `2.0` |
| `LOG_LEVEL` | 否 | 預設 `INFO`；設 `DEBUG` 可看逐檔明細 |

### LINE Bot

1. 前往 [LINE Developers Console](https://developers.line.biz/) 建立 Messaging API channel
2. 在 **Messaging API** 分頁取得 Channel Access Token，並用手機掃 QR code 加該 Bot 為好友
3. 個人 User ID 在 **Basic settings** 分頁最下方的 **Your user ID**（`U` 開頭）

### 用不同設定檔測試

測試時建議發給自己而非正式群組：

```bash
cp .env .env.development     # 再把 LINE_USER_ID 改成自己的 U 開頭 ID
python -m stock_notify --env-file .env.development
```

`.env.development` 已在 `.gitignore` 中（`.env.*` 規則）。

## 使用方法

```bash
python -m stock_notify                          # 分析今天
python -m stock_notify --date 2026-07-24        # 指定日期（驗證用）
python -m stock_notify --env-file .env.development
```

### Exit code

| 值 | 意義 |
|---|---|
| 0 | 成功（含「今日休市」） |
| 1 | 抓取、通知或未預期的錯誤 |
| 2 | 設定錯誤 |
| 130 | 使用者中斷 |

無法確認今日是否為交易日時（行事曆 API 連續失敗）會**中止並回傳 1**，而不是假設今天有開盤 —— 在休市日發出買賣訊號比不發更糟。

### 排程

`.github/workflows/daily.yaml` 於每個工作日 06:30 UTC（台北 14:30，收盤後）執行，並先跑 lint / 型別檢查 / 測試才執行分析。

需要在 repo 的 Secrets 設定 `MONGO_URI`、`LINE_TOKEN`、`LINE_USER_ID`、`VOLUME_MULTIPLIER`。

## 專案結構

```
src/stock_notify/
├── cli.py                  # 進入點，決定 exit code
├── config.py               # 常數與環境變數
├── models.py               # Signal / Bar / PriceHistory / StockAnalysis
├── pipeline.py             # 流程編排
├── trading_calendar.py     # 交易日判斷、推算過去交易日
├── analysis/               # 純運算，無 I/O
│   ├── pe.py               #   本益比判定
│   ├── kdj.py              #   KDJ 計算
│   └── signals.py          #   買賣規則（系統唯一的業務規則）
├── sources/
│   ├── common.py           #   共用解析工具與限流
│   ├── twse.py             #   上市
│   ├── tpex.py             #   上櫃
│   └── market.py           #   市場註冊表
├── storage/mongo.py        # 歷史價格儲存（symbol: .TW / .TWO）
└── notify/
    ├── formatting.py       #   訊息組裝（純函式）
    └── line.py             #   LINE 推播傳輸
tests/                      # 全離線，不需網路或資料庫
```

分層原則：`analysis/`、`notify/formatting.py`、`trading_calendar` 的判斷函式都是純函式，不做 I/O、不輸出訊息，因此所有業務規則都能直接單元測試。

## 開發

```bash
ruff check src tests && ruff format --check src tests
mypy
pytest
```

## 通知範例

```
📊 股票分析 v2 (2026-07-24)

🔴 買進建議
台積電 2330 * (PE: 18.5, J: 8.2)
緯創 3231 (PE: 15.3, J: 9.1)

🔵 賣出建議
鴻海 2317 (PE: 42.1, J: 91.5)

* 表示成交量異常放大
```

## 故障排除

| 症狀 | 檢查 |
|---|---|
| MI_INDEX 回傳非預期狀態 | 多半是超過 6 秒頻率限制被鎖 IP，等一小時 |
| 「收盤資料尚未公布」 | 證交所當日行情通常在收盤後一段時間才更新，稍後再執行 |
| LINE 通知失敗 | 確認 Bot 已被加為好友，且 `LINE_USER_ID` 前綴符合預期對象 |
| 每次執行都在回補歷史 | `MONGO_URI` 未設定或連線失敗，檢查啟動時的 warning |

## 授權

此專案僅供學習和個人使用，請勿用於商業用途。使用時請遵守相關法規和 API 使用條款。
