# 專案進度紀錄

最後更新：2026-06-13

## 系統現況：全自動運轉中

```
每天 01:00（台北）  Railway pipeline：爬 MOPS 前一日法說會 → 影音抽取 →
                    Groq Whisper 逐字稿 → Gemini 分析 → Notion → TG/DC 推播
每天 04:00（台北）  GitHub Actions：從 Notion + MOPS 重建網站資料 →
                    部署 GitHub Pages（含 SEO 靜態頁與 sitemap）
每天 15:30（本機）  IR_Backfill_June：補 6/1～6/12 因免費額度未完成的場次
                    （補齊後空轉，可刪除工作排程器中的此任務）
```

## 已完成

### 第一階段：本地 pipeline ✅
- MOPS 爬蟲（上市+上櫃）、PDF 下載
- 影音三層備援：官方 mp4 → MOPS 登載 YouTube → YouTube 搜尋（三重濾網：
  完整關鍵字＋片長>10分＋上傳日±45天）
- STT：Groq Whisper 優先（>24MB 自動切 20 分鐘段落），Gemini 備援，
  重複迴圈偵測截斷
- AI 分析：Gemini 四模型降級鏈（2.5-flash→2.5-flash-lite→2.0-flash→
  2.0-flash-lite，各模型每日額度分開計）→ Groq llama 最終備援
- Notion 寫入（upsert 不重複）、Telegram + Discord 推播

### 第二階段：雲端部署 ✅
- Railway：專案 investor-relations / service pipeline，cron UTC 17:00，
  跑完即關（月成本 < $1）
- GitHub：repo slzzzper666/investor-relations（公開，已驗證零金鑰外洩）
- 本機每日排程已停用，避免與雲端重複推播

### 第三階段：網站 ✅
- 正式網址：https://slzzzper666.github.io/investor-relations/
- 「墨夜帳冊」設計：列表 + 行事曆雙檢視、市值排序、當日 modal、
  2026/1 起的 MOPS 歷史與未來場次（1,977 筆）
- SEO：每場法說會獨立靜態頁（完整逐字稿在 HTML）、sitemap.xml、
  robots.txt、OG 標籤、JSON-LD 結構化資料
- Google Search Console 已驗證、sitemap 已提交

## 進行中：歷史補檔（2026-06-13 起）

用本地資源大規模回補過去法說會，不靠雲端 API 額度：
- **STT＝本地 faster-whisper large-v3**（`USE_LOCAL_WHISPER=1`，僅本機 .env；Railway 維持雲端）。
  GPU 點不起來（驅動 472.80 太舊、僅支援 CUDA 11.4，新版 ctranslate2 要 CUDA 12），
  目前跑 **CPU**（約 0.5–1x 即時）。要 GPU 提速 5–10 倍須更新 NVIDIA 驅動（待授權）。
- **分析＝Groq llama**（Gemini 免費額度今日歸零；已讓 PDF 路徑也能走 Groq）。
- `backfill_history.py`：市值大→小、月份新→舊，約 1700 場待補，連續失敗 15 次熔斷。
  以 `backfill_run.bat` 經 Start-Process 常駐執行（關掉對話也會跑）。
- 進度：processed.json 從 51 → 103+ 持續增加。

### 已修正
- media.py 只認 `.mp4` → 現也認 `.mp3/.m4a` 等（第一金等 irconference .mp3 之前被漏）。
- analyze.py：PDF 場次新增 Groq llama 備援；Gemini 全耗盡時直接跳 Groq。
- 體檢：逐字稿 0 異常；分析 102 筆僅 2 佔位（第一金已重跑修復、全家餐飲無來源無法補）。

### 待辦提醒
- 補一批後重建網站資料（或等每日 04:00 Action）。
- 想提速：授權更新 NVIDIA 驅動 → GPU Whisper 快 5–10 倍。
- 本機程式改動（local whisper / Groq PDF 備援 / .mp3 修正）尚未 commit/push，
  Railway 雲端仍跑舊版（待一併檢視 radar 子系統後再 push）。

## 待辦 / 未來計畫
- [ ] 觀察 Search Console 收錄狀況（預計 1～2 週開始收錄）
- [ ] 6 月補課完成後刪除 IR_Backfill_June 排程
- [ ] 流量起來後（目標：每日數十自然搜尋訪客）購買自訂網域 →
      綁定 GitHub Pages（舊網址自動 301）→ 申請 Google AdSense
- [ ] 建議：輪換曾在對話中明文出現的金鑰（TG bot token、Discord webhook）

## 金鑰存放位置（皆未進版控）
- 本機：`.env`
- Railway：service 環境變數
- GitHub：Actions encrypted secrets（僅 NOTION 兩把，供網站建置）
