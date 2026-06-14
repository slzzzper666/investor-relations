"""美股財報：龍頭白名單（Gemini 生成）＋ 個股財報數據（yfinance）＋ AI 中英分析。

供 site/build_us.py 產出網站用的 us_list.json / detail/us-*.json。
逐字稿（電話會議）暫不納入，待來源確定後再補。
"""
import json
import time
from datetime import date, datetime, timedelta

from google import genai
from google.genai import types
from pydantic import BaseModel

import config
from ir.gemini_util import generate_with_retry
from ir.logger import get_logger
from ir.radar.us import ZH_NAMES, _yahoo_symbol

log = get_logger("ir.us_earn")

WHITELIST_FILE = config.DATA_DIR / "us_whitelist.json"
ANALYSIS_DIR = config.DATA_DIR / "us_analysis"   # 每檔財報的 AI 分析快取


# ── 1. 龍頭白名單（Gemini 依三準則生成）────────────────────

class _WLItem(BaseModel):
    symbol: str
    name_zh: str
    sector: str
    theme: str          # 入選題材／理由（熱門題材或龍頭定位）


class _Whitelist(BaseModel):
    items: list[_WLItem]


_WL_SYSTEM = (
    "你是熟悉美股的研究主管，負責挑選『值得長期追蹤財報』的美股觀察名單。"
    "只輸出真實、目前仍在 NYSE/Nasdaq 掛牌的普通股代號（不要 ETF、不要已下市）。"
)

_WL_PROMPT = """請依以下三準則，挑選美股觀察白名單（約 70～90 檔）：
1. 市值規模到一定水準的大型權值股。
2. 市值未必大，但屬於近期熱門題材（如 AI、GPU、HBM、機器人、太空、減重藥、
   核能/SMR、資安、量子運算、電力基建等）的代表股。
3. 市值未必大，但屬於該產業的龍頭前 3～5 名。

涵蓋主要產業：半導體、AI硬體/伺服器、軟體/雲端、網路平台、消費、金融、
醫療生技、能源、工業、通訊、電動車與能源轉型等。

輸出 JSON（不要 markdown），格式：
{"items":[{"symbol":"NVDA","name_zh":"輝達","sector":"半導體","theme":"AI/GPU 龍頭"}, ...]}
name_zh 用台灣常見譯名（無慣用譯名則用英文公司簡稱）。"""


def generate_whitelist() -> list[dict]:
    """呼叫 Gemini 產生龍頭白名單並寫入 WHITELIST_FILE。"""
    client = genai.Client(api_key=config.GEMINI_API_KEY,
                          http_options=types.HttpOptions(timeout=120_000))
    resp = generate_with_retry(
        client,
        contents=[_WL_PROMPT],
        config=types.GenerateContentConfig(
            temperature=0.4,
            system_instruction=_WL_SYSTEM,
            response_mime_type="application/json",
            response_schema=_Whitelist,
        ),
    )
    items = [it.model_dump() for it in resp.parsed.items]
    # 去重（同代號保留先出現者），補上常見中文名
    seen, out = set(), []
    for it in items:
        sym = (it.get("symbol") or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        it["symbol"] = sym
        it["name_zh"] = ZH_NAMES.get(sym) or it.get("name_zh") or sym
        out.append(it)
    WHITELIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    WHITELIST_FILE.write_text(
        json.dumps({"generated_at": datetime.now().isoformat(), "items": out},
                   ensure_ascii=False, indent=1),
        encoding="utf-8")
    log.info("白名單已生成：%d 檔", len(out))
    return out


def load_whitelist(force: bool = False) -> list[dict]:
    """讀白名單；不存在或 force 時用 Gemini 重生。"""
    if not force and WHITELIST_FILE.exists():
        try:
            return json.loads(WHITELIST_FILE.read_text(encoding="utf-8"))["items"]
        except (ValueError, KeyError):
            pass
    return generate_whitelist()


# ── 2. 個股財報數據（yfinance）────────────────────────────

def _f(v):
    import pandas as pd
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_earnings(symbol: str, within_days: int = 45) -> dict | None:
    """抓最近一次已公布財報；超過 within_days 未公布回 None。"""
    import yfinance as yf

    t = yf.Ticker(_yahoo_symbol(symbol))
    try:
        ed = t.earnings_dates
    except Exception as e:  # noqa: BLE001
        log.warning("%s earnings_dates 失敗：%s", symbol, e)
        return None
    if ed is None or ed.empty:
        return None
    reported = ed.dropna(subset=["Reported EPS"]).sort_index()
    if reported.empty:
        return None
    row = reported.iloc[-1]
    report_date = reported.index[-1].date()
    if report_date < date.today() - timedelta(days=within_days):
        return None  # 太久沒公布，這次先不收

    info = {}
    try:
        info = t.info
    except Exception:  # noqa: BLE001
        pass

    data = {
        "symbol": symbol,
        "name_en": info.get("longName") or info.get("shortName") or symbol,
        "sector": info.get("sector", ""),
        "report_date": report_date.isoformat(),
        "eps_actual": _f(row.get("Reported EPS")),
        "eps_estimate": _f(row.get("EPS Estimate")),
        "surprise_pct": _f(row.get("Surprise(%)")),
        "pe": _f(info.get("trailingPE")),
        "market_cap": _f(info.get("marketCap")),
    }

    try:
        inc = t.quarterly_income_stmt
        if inc is not None and "Total Revenue" in inc.index:
            revs = inc.loc["Total Revenue"].dropna()
            if len(revs) >= 1:
                data["revenue"] = _f(revs.iloc[0])
                if len(revs) >= 5 and revs.iloc[4]:
                    data["revenue_yoy"] = (revs.iloc[0] / revs.iloc[4] - 1) * 100
    except Exception:  # noqa: BLE001
        pass

    for k in ("postMarketChangePercent", "regularMarketChangePercent"):
        if info.get(k) is not None:
            data["price_reaction"] = _f(info[k])
            break
    return data


# ── 3. AI 分析（Gemini，中英雙語）─────────────────────────

class _USAnalysis(BaseModel):
    one_liner_zh: str
    one_liner_en: str
    beat_or_miss: str          # 超預期 / 符合預期 / 低於預期
    highlights_zh: list[str]
    highlights_en: list[str]
    risks_zh: list[str]
    risks_en: list[str]
    ai_view_zh: str
    ai_view_en: str


_AN_SYSTEM = (
    "You are a senior US equity earnings analyst. Output strictly valid JSON. "
    "Chinese fields use Traditional Chinese (Taiwan); English fields use English. "
    "Be professional, concise, objective; no emoji."
)

_AN_PROMPT = """Analyse the latest quarterly earnings of {name} ({symbol}).

Report date: {report_date}
Sector: {sector}
EPS actual: {eps_actual}  estimate: {eps_estimate}  surprise: {surprise}
Revenue: {revenue}  YoY: {rev_yoy}
Post-report price reaction: {price}  Trailing PE: {pe}

Return JSON (no markdown fence):
{{
 "one_liner_zh":"一句話總結（60字內）","one_liner_en":"one-sentence summary",
 "beat_or_miss":"超預期/符合預期/低於預期 擇一，附一句關鍵說明",
 "highlights_zh":["3~5 條財報亮點"],"highlights_en":["3-5 highlights"],
 "risks_zh":["2~3 條風險"],"risks_en":["2-3 risks"],
 "ai_view_zh":"觀點：訊號、對股價短中期意涵、追蹤指標（200字內）",
 "ai_view_en":"view: signal, near/mid-term implication, what to track"
}}"""


def _fmt(v, suffix="", money=False):
    if v is None:
        return "N/A"
    if money:
        if abs(v) >= 1e9:
            return f"${v / 1e9:.2f}B"
        if abs(v) >= 1e6:
            return f"${v / 1e6:.1f}M"
        return f"${v:,.0f}"
    return f"{v:.2f}{suffix}"


def analyze(data: dict) -> dict:
    prompt = _AN_PROMPT.format(
        name=data.get("name_en") or data["symbol"], symbol=data["symbol"],
        report_date=data["report_date"], sector=data.get("sector") or "—",
        eps_actual=_fmt(data.get("eps_actual")),
        eps_estimate=_fmt(data.get("eps_estimate")),
        surprise=_fmt(data.get("surprise_pct"), "%"),
        revenue=_fmt(data.get("revenue"), money=True),
        rev_yoy=_fmt(data.get("revenue_yoy"), "%"),
        price=_fmt(data.get("price_reaction"), "%"),
        pe=_fmt(data.get("pe")),
    )
    client = genai.Client(api_key=config.GEMINI_API_KEY,
                          http_options=types.HttpOptions(timeout=120_000))
    resp = generate_with_retry(
        client,
        contents=[prompt],
        config=types.GenerateContentConfig(
            temperature=0.3,
            system_instruction=_AN_SYSTEM,
            response_mime_type="application/json",
            response_schema=_USAnalysis,
        ),
    )
    return resp.parsed.model_dump()


def analyze_cached(data: dict) -> dict:
    """同一檔財報（symbol+report_date）的分析快取，避免重複呼叫 Gemini。"""
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    key = f"{data['symbol']}_{data['report_date']}"
    cache = ANALYSIS_DIR / f"{key}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    result = analyze(data)
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    time.sleep(1)
    return result
