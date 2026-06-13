# 財報雷達子系統（radar）

整合自獨立的「財報雷達」專案，作為 IR 專案底下的並列子系統。負責**事件行事曆 + 量化結果**，與既有的法說會深度整理（`main.py`）互補：

| | 既有 `main.py`（法說會整理） | 新增 `radar.py`（財報雷達） |
|---|---|---|
| 做什麼 | 法說會影片→STT→AI 分析→Notion/網站 | 財報/總經行事曆 + 公布後數據 vs 預期 |
| 範圍 | 台股法說會深度內容 | 台股財報、美股財報、美台總經數據 |
| 輸出 | Notion + GitHub Pages + TG/DC | TG/DC 推播 |

## 用法

```
python radar.py --calendar   # 推播今日～下週五的財報與總經行事曆
python radar.py --result     # 抓最新公布的財報/總經結果並推播（vs 預期，✅/❌/➡️）
python radar.py --test       # 行事曆 + 結果各跑一次
python radar.py --daemon     # 常駐：08:00 台北時間推行事曆，每 15 分鐘輪詢結果
```

## 架構（共用 IR 基礎設施，無重複）

```
radar.py                  進入點 + 排程（去重 state: data/radar_state.json）
ir/radar/
├── models.py             TwEarning / UsEarning / MacroEvent
├── formatter.py          推播版面（行事曆、財報結果、總經結果）
├── tw.py                 台股：法說會行事曆複用 ir.mops；財報結果走 MOPS 新版 JSON API
├── us.py                 美股：Nasdaq 行事曆 ∩ S&P500 + yfinance 結果
└── macro.py              總經：Investing.com 經濟日曆（美/台，前值/預期/實際）
```

整合時刪除的重複（改用 IR 既有元件）：
- HTTP/Proxy 偵測 → `ir.net.get_session()`
- logging → `ir.logger.get_logger()`
- 金鑰/時區 → `config`（TG/DC 共用 IR 既有，新增 `TZ_*` 與選用 key）
- TG/DC 純文字推播 → `ir.notify.push_text()`
- **台股法說會行事曆 → `ir.mops.get_conferences_in_range()`**（與 `main.py` 共用同一份 MOPS 法說會資料）

## 資料來源（皆免費、已實測）

- 台股財報結果：MOPS 新版 JSON API（重訊偵測「董事會通過財務報告」→ t163sb01 損益表）。台股無免費市場預期 EPS → 以「去年同期」為比較基準。
- 美股財報：Nasdaq calendar API（行事曆/盤前盤後/預期 EPS）+ yfinance（EPS actual/surprise、營收 financialsChart、盤後漲跌）。
- 總經數據：Investing.com `getCalendarFilteredData`（美國 CPI/PPI/FOMC/非農…+ 台灣央行/CPI/進出口/外銷訂單）。

## 部署（Railway）

`main.py`（法說會 cron，每天台北 01:00）維持不動。radar 因排程節奏不同，建議在**同一個 repo 開第二個 Railway service**：

- **方式 A（常駐，推薦）**：新 service，start command = `python radar.py --daemon`，`restartPolicyType = ALWAYS`。程式內建 08:00 行事曆 + 15 分鐘輪詢結果。
- **方式 B（cron）**：開兩個 cron service：
  - `python radar.py --calendar`，cronSchedule `0 0 * * *`（台北 08:00 = UTC 00:00）
  - `python radar.py --result`，較密集排程（如每 30 分）抓結果。

兩種方式都共用同一份程式碼、`requirements.txt`（已加 `pandas`/`yfinance`）與環境變數。注意容器檔案系統為暫時性，`data/radar_state.json` 重新部署後會重置，可能短暫重複推播一次最近結果；要避免可掛 Railway Volume 到 `/app/data`。
