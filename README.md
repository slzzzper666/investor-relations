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
data/              音檔、逐字稿、PDF、處理紀錄（不進版控）
```
