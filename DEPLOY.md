# Railway 部署指南

每天台北時間 01:00 由 Railway Cron 自動執行 `python main.py`，處理前一天的法說會。

## 1. 推上 GitHub

```cmd
cd /d D:\AI\Investor_Relations
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/<你的帳號>/investor-relations.git
git push -u origin main
```

- `.env` 已在 `.gitignore`，金鑰不會進版控。推送前可用 `git status` 確認沒有 `.env`、`data/`、`.venv/`。
- 建議開 **Private** repo。

## 2. Railway 建立服務

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** → 選這個 repo。
2. Railway 會讀取 `railway.toml`：用 `Dockerfile` 建置、套用 cron 排程，無需手動調整 Build 設定。

## 3. 設定環境變數

Service → **Variables**，逐一新增（值從本地 `.env` 複製）：

| 變數 | 必填 | 說明 |
|---|---|---|
| `GEMINI_API_KEY` | 是 | STT + AI 分析 |
| `NOTION_API_KEY` | 是 | Notion Integration Token |
| `NOTION_PARENT_ID` | 是 | 既有 Notion 資料庫 ID（與本地 `.env` 相同） |
| `TELEGRAM_BOT_TOKEN` | 推播擇一 | Telegram Bot |
| `TELEGRAM_CHAT_ID` | 推播擇一 | Telegram 目標聊天室 |
| `DISCORD_WEBHOOK_URL` | 推播擇一 | Discord Webhook |
| `GROQ_API_KEY` | 否 | 填了就改用 Groq Whisper 做 STT |

雲端不需要 `.env` 檔：`config.py` 的 `load_dotenv` 找不到檔案時會自動讀系統環境變數。

## 4. 確認 Cron 排程

- Service → **Settings** → **Cron Schedule** 應顯示 `0 17 * * *`（UTC 17:00 = 台北 01:00）。
- Cron 模式下服務平時不運行，到點才啟動容器跑一次，跑完即結束（`restartPolicyType = NEVER`）。
- 想立即測試：Service → **Deployments** → 最新部署右側選單 → **Restart**（會立刻跑一次 `main.py`），或先在本地 `python main.py --limit 1 --no-push` 驗證。

## 5. 看 Log

- Service → **Deployments** → 點開某次執行 → **Deploy Logs**，即 `main.py` 的 stdout 輸出。
- 每次執行結尾會印 `===== 完成：成功 X、失敗 Y、跳過 Z =====`，以此確認當天結果。
- 容器內也會寫 `investor_relations.log`，但容器是暫時性的，看 Railway log 即可。

## 注意事項

- **檔案系統是暫時性的**：`data/processed.json`（已處理紀錄）每次執行都是全新的。因為每天只跑一次且只處理「昨天」，正常不會重複；Notion 本身是 upsert（同公司同日期會更新不會重複新增），但**手動重跑同一天會重複推播 TG/DC**。若在意，可掛 Railway **Volume** 到 `/app/data` 保留紀錄。
- ffmpeg 由 `imageio-ffmpeg` 的 pip wheel 內建提供，映像不需另外安裝。
