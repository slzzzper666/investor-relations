@echo off
rem 歷史法說會補檔 — 獨立常駐執行（不受 Claude 工具逾時影響）
cd /d D:\AI\Investor_Relations
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe backfill_history.py >> backfill_console.log 2>&1
