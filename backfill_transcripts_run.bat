@echo off
chcp 65001 >nul
cd /d D:\AI\Investor_Relations
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -u backfill_transcripts.py --since 2026-06-17 --until 2026-09-08 >> data\backfill_transcripts.log 2>&1
