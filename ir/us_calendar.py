"""美股財報行事曆與代號工具（自財報雷達子系統保留下來的部分）。

行事曆：Nasdaq Calendar API（免 key，需瀏覽器 headers）∩ S&P 500 成分股，
供 build_us 產出 us_upcoming.json。財報雷達（TG/DC 推播）已於 2026-09 移除，
只留這裡 build_us / us_earn 還在用的東西。
"""
import csv
import io
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

from config import DATA_DIR
from ir.logger import get_logger
from ir.net import get_session

log = get_logger("ir.us_calendar")


@dataclass
class UsEarning:
    """美股財報事件（行事曆用）。"""
    symbol: str
    name: str
    date: str                # YYYY-MM-DD（美東日期）
    session: str = ""        # BMO（盤前）/ AMC（盤後）/ 空字串未知
    eps_estimate: float | None = None


NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings"
NASDAQ_HEADERS = {  # User-Agent 由共用 session 提供
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
SP500_CSV = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
    "main/data/constituents.csv"
)
SP500_CACHE = DATA_DIR / "sp500.json"
SP500_TTL_DAYS = 7
ZH_NAMES = {
    "NVDA": "輝達", "AAPL": "蘋果", "MSFT": "微軟", "GOOGL": "Alphabet",
    "GOOG": "Alphabet", "AMZN": "亞馬遜", "META": "Meta", "TSLA": "特斯拉",
    "AVGO": "博通", "AMD": "超微", "INTC": "英特爾", "QCOM": "高通",
    "TSM": "台積電ADR", "MU": "美光", "TXN": "德州儀器", "ORCL": "甲骨文",
    "CRM": "Salesforce", "ADBE": "Adobe", "NFLX": "網飛", "DIS": "迪士尼",
    "JPM": "摩根大通", "GS": "高盛", "MS": "摩根士丹利", "BAC": "美國銀行",
    "WMT": "沃爾瑪", "COST": "好市多", "KO": "可口可樂", "PEP": "百事",
    "MCD": "麥當勞", "NKE": "Nike", "SBUX": "星巴克", "BA": "波音",
    "CAT": "開拓重工", "XOM": "埃克森美孚", "CVX": "雪佛龍",
    "PFE": "輝瑞", "JNJ": "嬌生", "LLY": "禮來", "UNH": "聯合健康",
    "V": "Visa", "MA": "萬事達", "PYPL": "PayPal", "UBER": "Uber",
}


# ── S&P 500 成分股 ────────────────────────────────────────


def _load_sp500() -> set[str]:
    """取得 S&P 500 代號集合，快取 7 天；抓取失敗時退用舊快取。"""
    if SP500_CACHE.exists():
        try:
            cached = json.loads(SP500_CACHE.read_text(encoding="utf-8"))
            age = datetime.now() - datetime.fromisoformat(cached["fetched_at"])
            if age < timedelta(days=SP500_TTL_DAYS):
                return set(cached["symbols"])
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    try:
        r = get_session().get(SP500_CSV, timeout=20)
        r.raise_for_status()
        rows = csv.DictReader(io.StringIO(r.text))
        symbols = {row["Symbol"].strip() for row in rows if row.get("Symbol")}
        if len(symbols) < 400:
            raise ValueError(f"S&P500 清單僅 {len(symbols)} 檔，疑似格式變動")
        SP500_CACHE.write_text(
            json.dumps(
                {"fetched_at": datetime.now().isoformat(), "symbols": sorted(symbols)}
            ),
            encoding="utf-8",
        )
        return symbols
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning("S&P500 清單抓取失敗：%s，嘗試使用舊快取", e)
        if SP500_CACHE.exists():
            return set(json.loads(SP500_CACHE.read_text(encoding="utf-8"))["symbols"])
        return set()


# ── Nasdaq 行事曆 ─────────────────────────────────────────


def _parse_money(s: str | None) -> float | None:
    """Nasdaq 金額字串 → float。'$0.37'→0.37、'($0.06)'→-0.06、''→None。"""
    if not s:
        return None
    t = s.replace("$", "").replace(",", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _clean_name(name: str) -> str:
    return re.sub(
        r",?\s+(Inc\.?|Corporation|Corp\.?|Company|Co\.?|plc|Ltd\.?|"
        r"Incorporated|Common Stock|Class [A-C])\s*$",
        "",
        name.strip(),
        flags=re.IGNORECASE,
    )


def _fetch_nasdaq_day(session: requests.Session, d: date) -> list[dict]:
    for attempt in range(3):
        try:
            r = session.get(
                NASDAQ_URL,
                params={"date": d.isoformat()},
                headers=NASDAQ_HEADERS,
                timeout=15,
            )
            r.raise_for_status()
            return (r.json().get("data") or {}).get("rows") or []
        except (requests.RequestException, json.JSONDecodeError) as e:
            log.warning("Nasdaq 行事曆 %s 第 %d 次失敗：%s", d, attempt + 1, e)
            time.sleep(2)
    return []


def _session_code(nasdaq_time: str) -> str:
    return {
        "time-pre-market": "BMO",
        "time-after-hours": "AMC",
    }.get(nasdaq_time, "")


def _rows_to_earnings(rows: list[dict], d: date, sp500: set[str]) -> list[UsEarning]:
    out = []
    for row in rows:
        sym = (row.get("symbol") or "").strip()
        if not sym or (sp500 and sym not in sp500):
            continue
        name = ZH_NAMES.get(sym) or _clean_name(row.get("name") or sym)
        out.append(
            UsEarning(
                symbol=sym,
                name=name,
                date=d.isoformat(),
                session=_session_code(row.get("time") or ""),
                eps_estimate=_parse_money(row.get("epsForecast")),
            )
        )
    return out


def fetch_calendar(start: date, end: date) -> list[UsEarning]:
    """抓取 start～end（視為美東日期）之間 S&P 500 成分股的財報行事曆。"""
    sp500 = _load_sp500()
    if not sp500:
        log.warning("S&P500 清單不可用，行事曆將涵蓋全部美股（筆數可能偏多）")
    session = get_session()
    events: list[UsEarning] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            rows = _fetch_nasdaq_day(session, d)
            events.extend(_rows_to_earnings(rows, d, sp500))
            time.sleep(0.6)
        d += timedelta(days=1)
    log.info("美股行事曆：S&P500 共 %d 筆", len(events))
    return events


def _yahoo_symbol(sym: str) -> str:
    return sym.replace(".", "-")


