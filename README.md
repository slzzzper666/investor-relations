# 法說會自動整理系統

零人工介入：每天自動爬取台灣上市櫃法說會 → 抽影音 → 逐字稿 → AI 分析 → Notion 歸檔 → Telegram/Discord 推播。

## 流程

```
MOPS 公開資訊觀測站（前一天的法說會公告，上市+上櫃）
  │  公司、代號、時間地點、簡報 PDF、影音連結
  ▼
影音來源解析（ir/media.py）
  1. MOPS 登載的官方 mp4（irconference.twse.com.tw，優先中文版）
  2. MOPS 登載的 YouTube 連結
  3. yt-dlp ytsearch 搜尋（標題含公司名+法說關鍵字、片長>10分鐘才採用）
  → ffmpeg 抽成 16kHz 單聲道 mp3
  ▼
語音轉文字（ir/stt.py）
  Gemini 2.5 Flash（預設）；.env 填 GROQ_API_KEY 則優先用 Groq Whisper
  ▼
AI 分析（ir/analyze.py）
  一句話總結 / 重點摘要 / 公司展望 / AI 觀點（structured output）
  找不到影音時退用簡報 PDF 分析
  ▼
Notion 資料庫（一場法說會一行）＋ Telegram、Discord 推播
```

## 使用

```cmd
.venv\Scripts\python main.py                    # 處理昨天（台北時間）
.venv\Scripts\python main.py --date 2026-06-11  # 指定日期
.venv\Scripts\python main.py --limit 3 --no-push  # 測試用
```

- 排程：Windows 工作排程器每天 01:00 執行 `run_daily.bat`
- Log：CMD 即時輸出 + `investor_relations.log`
- 已處理紀錄：`data/processed.json`（重跑不會重複推播）
- 金鑰：全部在 `.env`（已加入 .gitignore，不會進版控）
- Proxy：自動偵測，直連失敗才走 `FALLBACK_PROXY`

## 專案結構

```
main.py            主流程（每家公司獨立容錯）
config.py          .env 載入與路徑
ir/mops.py         階段一：MOPS 爬蟲 + PDF 下載
ir/media.py        階段二：影音來源解析 + 音檔萃取
ir/stt.py          階段三：語音轉文字
ir/analyze.py      階段四：AI 分析
ir/notion_db.py    階段五：Notion 寫入（upsert）
ir/notify.py       階段五：TG/DC 推播
ir/net.py          Proxy 自動偵測
ir/logger.py       雙輸出 logging（Asia/Taipei）
ir/fin6q.py        近 6 季財報序列（FinMind）
ir/industry.py     官方產業別 + 同業本益比中位數
ir/business.py     業務項目萃取（Gemini 讀簡報 PDF 的圖表）
data/              音檔、逐字稿、PDF、處理紀錄（不進版控）
data/business/     業務項目成果（進版控，供網站建置使用）
```

## 網站資料建置

```cmd
.venv\Scripts\python site\build_data.py        台股：Notion → list.json / detail / 靜態頁
.venv\Scripts\python site\build_business.py    業務項目：近 7 天的公司（AI 讀簡報）
.venv\Scripts\python site\build_us.py          美股
.venv\Scripts\python site\build_macro.py       總經
```

**財報數據**（詳細頁右欄）：近 6 季單季營收／EPS／毛利率／資本支出，加上本益比
與同業中位數的折溢價比較。資料來自 FinMind（單季值、免金鑰），逐檔快取於
`site/.fin6q_cache.json`。FinMind 免費版有每小時請求上限，`build_data.py` 每輪
預設只補 120 檔（`IR_FIN_LIMIT` 可調），依日期新→舊優先，跑幾輪就補滿。
雲端（GitHub Actions）設 `IR_SKIP_FINANCIALS=1` 全用已提交的快取、不連外。

**業務項目**（產品別營收比重＋族群標籤）：比重多半畫在簡報的圓餅圖裡，純文字
抽不到，所以把 PDF 交給 Gemini 多模態判讀。族群標籤限定 `ir/business.py` 的
`TAXONOMY` 字彙，跨公司才連得起來（點族群 → 首頁篩選同族群）。
**在本機跑、成果 commit 進版控**（同逐字稿分段的做法）；CI 不跑，因為 Actions
產生的檔案不會回推 repo，每天重跑等於重複燒 AI 額度。
