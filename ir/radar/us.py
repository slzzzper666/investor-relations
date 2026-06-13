"""美股財報模組。

行事曆：Nasdaq Calendar API（免 key，需瀏覽器 headers）∩ S&P 500 成分股。
結果：yfinance earnings_dates（EPS est/actual/surprise）+ Yahoo financialsChart（當晚更新的實際營收）。
營收預期：行事曆階段預存（公布後 Yahoo 的 0q 會滾到下一季，事後抓會錯季）。
盤後反應：yfinance info 的 pre/postMarketChangePercent。
詳見 research/us_findings.md。
"""
import csv
import io
import json
import re
import time
from datetime import date, datetime, timedelta

import requests

from config import DATA_DIR, TZ_US_EAST
from ir.logger import get_logger
from ir.net import get_session
from ir.radar.models import UsEarning

log = get_logger("ir.radar.us")

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
ESTIMATES_FILE = DATA_DIR / "us_estimates.json"

MAX_RESULT_CANDIDATES = 25  # 每輪結果輪詢最多查幾檔（依市值排序）

# 常見公司中文名（推播可讀性用，未列入者顯示英文名）
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
    _store_estimates(events)
    return events


# ── 營收預期預存 ──────────────────────────────────────────
# Yahoo 的 revenue_estimate「0q」在財報公布後會滾到下一季，
# 所以必須在公布前（每日行事曆階段）先存下本季的預期與去年同期值。


def _load_estimates() -> dict:
    if ESTIMATES_FILE.exists():
        try:
            return json.loads(ESTIMATES_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _store_estimates(events: list[UsEarning], horizon_days: int = 3) -> None:
    """為 horizon_days 內即將公布的公司預存營收預期與去年同期營收。"""
    imminent = [
        e for e in events
        if date.fromisoformat(e.date) <= date.today() + timedelta(days=horizon_days)
    ]
    if not imminent:
        return
    try:
        import pandas as pd
        import yfinance as yf
    except ImportError:
        log.warning("yfinance 未安裝，略過營收預期預存")
        return

    store = _load_estimates()
    for e in imminent:
        key = f"{e.symbol}:{e.date}"
        if key in store:
            continue
        entry = {"stored_at": datetime.now().isoformat()}
        try:
            est = yf.Ticker(_yahoo_symbol(e.symbol)).revenue_estimate
            if est is not None and "0q" in est.index:
                if pd.notna(est.loc["0q", "avg"]):
                    entry["rev_est"] = float(est.loc["0q", "avg"])
                if pd.notna(est.loc["0q", "yearAgoRevenue"]):
                    entry["rev_year_ago"] = float(est.loc["0q", "yearAgoRevenue"])
        except Exception as ex:
            log.warning("%s 營收預期預存失敗：%s", e.symbol, ex)
        store[key] = entry
        time.sleep(1.0)

    # 清掉 30 天前的舊條目
    cutoff = datetime.now() - timedelta(days=30)
    store = {
        k: v for k, v in store.items()
        if datetime.fromisoformat(v.get("stored_at", "2000-01-01")) > cutoff
    }
    ESTIMATES_FILE.write_text(
        json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    log.info("美股營收預期預存完成（%d 檔即將公布）", len(imminent))


# ── Yahoo quoteSummary（cookie + crumb）──────────────────

_YQ: dict = {"session": None, "crumb": ""}


def _yahoo_symbol(sym: str) -> str:
    return sym.replace(".", "-")


def _refresh_crumb() -> None:
    s = get_session()
    try:
        s.get("https://fc.yahoo.com", timeout=10)  # 目的只在 set-cookie，404 正常
    except requests.RequestException:
        pass
    r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10)
    _YQ["session"], _YQ["crumb"] = s, r.text.strip()


def _quote_summary(symbol: str, modules: str) -> dict:
    """帶 crumb 的 quoteSummary 查詢，401 時重新取 crumb 一次。"""
    for attempt in range(2):
        if _YQ["session"] is None or not _YQ["crumb"]:
            _refresh_crumb()
        r = _YQ["session"].get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
            params={"modules": modules, "crumb": _YQ["crumb"]},
            timeout=15,
        )
        if r.status_code == 401 and attempt == 0:
            _YQ["session"] = None
            continue
        r.raise_for_status()
        results = (r.json().get("quoteSummary") or {}).get("result") or []
        return results[0] if results else {}
    return {}


def _fresh_revenue_from_chart(symbol: str) -> float | None:
    """financialsChart 在財報公布當晚即更新，最後一筆即剛公布季度的實際營收。"""
    try:
        data = _quote_summary(_yahoo_symbol(symbol), "earnings")
        quarters = ((data.get("earnings") or {}).get("financialsChart") or {}).get(
            "quarterly"
        ) or []
        if quarters:
            return (quarters[-1].get("revenue") or {}).get("raw")
    except (requests.RequestException, json.JSONDecodeError) as e:
        log.warning("%s financialsChart 失敗：%s", symbol, e)
    return None


# ── 財報結果 ──────────────────────────────────────────────


def _candidates(session: requests.Session, sp500: set[str]) -> list[UsEarning]:
    """昨天（AMC 已出爐）+ 今天（BMO 已出爐）的美東行事曆，依市值取前 N 檔。"""
    us_today = datetime.now(TZ_US_EAST).date()
    cands: list[tuple[float, UsEarning]] = []
    for d in (us_today - timedelta(days=1), us_today):
        for row in _fetch_nasdaq_day(session, d):
            sym = (row.get("symbol") or "").strip()
            if not sym or (sp500 and sym not in sp500):
                continue
            sess = _session_code(row.get("time") or "")
            # 今天的 AMC 還沒公布，略過；其餘（昨日全部、今日 BMO/未標示）都檢查
            if d == us_today and sess == "AMC":
                continue
            cap = _parse_money(row.get("marketCap")) or 0.0
            ev = UsEarning(
                symbol=sym,
                name=ZH_NAMES.get(sym) or _clean_name(row.get("name") or sym),
                date=d.isoformat(),
                session=sess,
                eps_estimate=_parse_money(row.get("epsForecast")),
            )
            cands.append((cap, ev))
        time.sleep(0.6)
    cands.sort(key=lambda x: -x[0])
    return [ev for _, ev in cands[:MAX_RESULT_CANDIDATES]]


def _fill_result_from_yf(ev: UsEarning, estimates: dict) -> bool:
    """用 yfinance + financialsChart 查實際 EPS/營收/盤後反應。已公布回 True。"""
    import pandas as pd
    import yfinance as yf

    t = yf.Ticker(_yahoo_symbol(ev.symbol))

    # 1. EPS actual / estimate / surprise（earnings_dates 的 Surprise(%) 已是百分比）
    try:
        ed = t.earnings_dates
    except Exception as e:
        log.warning("%s earnings_dates 取得失敗：%s", ev.symbol, e)
        return False
    if ed is None or ed.empty:
        return False
    recent = ed[ed.index.tz_convert(TZ_US_EAST).date >= (
        datetime.now(TZ_US_EAST).date() - timedelta(days=4)
    )]
    reported = recent.dropna(subset=["Reported EPS"])
    if reported.empty:
        return False  # 尚未公布
    row = reported.sort_index().iloc[-1]
    ev.eps_actual = float(row["Reported EPS"])
    if pd.notna(row.get("EPS Estimate")):
        ev.eps_estimate = float(row["EPS Estimate"])
    if pd.notna(row.get("Surprise(%)")):
        ev.surprise_pct = float(row["Surprise(%)"])

    # 2. 營收：實際用 financialsChart（公布當晚即更新）；
    #    預期與去年同期用行事曆階段預存值（事後抓 0q 會錯季）
    ev.revenue_actual = _fresh_revenue_from_chart(ev.symbol)
    stored = estimates.get(f"{ev.symbol}:{ev.date}") or {}
    if stored.get("rev_est") is not None:
        ev.revenue_estimate = stored["rev_est"]
    year_ago = stored.get("rev_year_ago")
    if ev.revenue_actual is not None and year_ago:
        ev.revenue_yoy = (ev.revenue_actual / year_ago - 1) * 100

    # 3. 盤前/盤後反應（info 的 ChangePercent 單位已是 %）
    try:
        info = t.info
        for key in ("postMarketChangePercent", "preMarketChangePercent"):
            v = info.get(key)
            if v is not None:
                ev.price_reaction_pct = float(v)
                break
    except Exception:
        pass
    return True


def fetch_results() -> list[UsEarning]:
    """抓取最近公布的 S&P 500 財報結果（已公布者）。"""
    sp500 = _load_sp500()
    session = get_session()
    cands = _candidates(session, sp500)
    log.info("美股結果候選 %d 檔", len(cands))
    estimates = _load_estimates()
    results = []
    for ev in cands:
        try:
            if _fill_result_from_yf(ev, estimates):
                results.append(ev)
        except Exception as e:
            log.warning("%s 結果抓取失敗：%s", ev.symbol, e)
        time.sleep(1.2)  # Yahoo 節流
    log.info("美股已公布結果 %d 檔", len(results))
    return results
