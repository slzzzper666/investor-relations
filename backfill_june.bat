@echo off
rem 六月報告補課（每天 15:30 Gemini 額度重置後執行；全部補齊後自動空轉）
cd /d D:\AI\Investor_Relations
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe main.py --date 2026-06-12
for %%d in (01 02 03 04 05 08 09 10) do (
  .venv\Scripts\python.exe main.py --date 2026-06-%%d --no-push
)
