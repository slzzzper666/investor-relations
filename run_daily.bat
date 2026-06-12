@echo off
rem 每日法說會自動整理（由 Windows 工作排程器於 01:00 觸發）
cd /d D:\AI\Investor_Relations
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe main.py
