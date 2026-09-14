@echo off
chcp 65001 >nul
cd /d D:\AI\Investor_Relations
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -u backfill_compare.py >> data\backfill_compare.log 2>&1
