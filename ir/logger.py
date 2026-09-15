"""統一 logging：同時輸出到 CMD 與 investor_relations.log，時間用 Asia/Taipei。

金鑰遮罩：requests 的例外訊息會帶完整網址（例如 api.telegram.org/bot<TOKEN>/…、
FRED 的 ?api_key=…），直接 log 出去就等於把 token 寫進日誌——repo 是公開的，
Actions 日誌人人可看。Formatter 在輸出前把所有已知的金鑰值換成 ***。
"""
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")

LOG_FILE = "investor_relations.log"

# 讀環境變數而非 import config，避免循環匯入；.env 由 config 載入，之後每次 format 都重讀（很便宜）
_SECRET_ENV = ("GEMINI_API_KEY", "GROQ_API_KEY", "TELEGRAM_BOT_TOKEN", "DISCORD_WEBHOOK_URL",
               "NOTION_API_KEY", "FRED_API_KEY", "ALPHA_VANTAGE_KEY", "GITHUB_TOKEN",
               *[f"GEMINI_API_KEY_{i}" for i in range(2, 10)])


def redact(text: str) -> str:
    """把已知金鑰值換成 ***（也處理 Discord webhook 的 id/token 段）。"""
    for name in _SECRET_ENV:
        v = os.getenv(name, "").strip()
        if len(v) >= 8 and v in text:
            text = text.replace(v, "***")
    return text


class TaipeiFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=TAIPEI)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")

    def format(self, record):
        return redact(super().format(record))


def get_logger(name: str = "ir") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = TaipeiFormatter("[%(asctime)s] %(levelname)s %(name)s - %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger
