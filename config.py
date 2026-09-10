"""讀取 .env 設定，集中管理所有金鑰與參數。"""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
AUDIO_DIR = DATA_DIR / "audio"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"

for d in (DATA_DIR, AUDIO_DIR, TRANSCRIPT_DIR):
    d.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


def _gemini_keys() -> list[str]:
    """所有可用的 Gemini 金鑰（GEMINI_API_KEY、GEMINI_API_KEY_2、_3…）。

    免費額度是「每金鑰每模型每日」分開計，多掛幾組不同 Google 帳號的金鑰，
    可用量就等倍數放大。順序即優先序，去重避免重複計算額度。
    """
    keys, seen = [], set()
    for name in ["GEMINI_API_KEY"] + [f"GEMINI_API_KEY_{i}" for i in range(2, 10)]:
        k = os.getenv(name, "").strip()
        if k and k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


GEMINI_API_KEYS = _gemini_keys()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_PARENT_ID = os.getenv("NOTION_PARENT_ID", "")  # 既有 Notion 資料庫的 ID

# 本地 Whisper（僅本機補檔用；Railway 雲端不設此旗標、維持走 Groq/Gemini）
USE_LOCAL_WHISPER = bool(os.getenv("USE_LOCAL_WHISPER", ""))

# ── 財報雷達子系統（radar）：選用 API key 與時區 ──────────
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")

TZ_TAIPEI = ZoneInfo("Asia/Taipei")
TZ_US_EAST = ZoneInfo("America/New_York")
TZ_UTC = ZoneInfo("UTC")
