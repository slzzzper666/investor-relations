"""美股公司輪廓：近 N 季財報序列、產業分類、業務族群（與台股頁同一套版面）。

資料全部來自 yfinance（免金鑰）：
  quarterly_income_stmt   5～7 季：Total Revenue / Gross Profit / Diluted EPS
  quarterly_cashflow      6 季：Capital Expenditure
  earnings_dates          24 季已公布 EPS（Reported EPS ＝市場看的調整後 EPS，附預期與驚奇）
  info                    sector / industry / trailingPE / longBusinessSummary

每季 EPS 用 earnings_dates 的 Reported EPS（非損益表的 Diluted EPS），因為卡片上的
「EPS vs 預期」就是它，兩處要一致；對應方式＝財報公布日落在該季季底後 75 天內。
產業用 Yahoo 的 sector（11 類，對應中文）當「產業別」層，industry 保留英文當補充。
族群標籤由 Gemini 讀 longBusinessSummary 從共用字彙挑（同台股，跨市場可比）。
"""
import json
import time
from datetime import timedelta

from google.genai import types
from pydantic import BaseModel, Field

from ir.business import TAXONOMY
from ir.gemini_util import all_exhausted, generate_with_retry
from ir.logger import get_logger

log = get_logger("ir.us_profile")

QUARTERS = 6

SECTOR_ZH = {
    "Technology": "科技",
    "Communication Services": "通訊服務",
    "Consumer Cyclical": "非必需消費",
    "Consumer Defensive": "必需消費",
    "Healthcare": "醫療保健",
    "Financial Services": "金融",
    "Industrials": "工業",
    "Energy": "能源",
    "Basic Materials": "原物料",
    "Real Estate": "不動產",
    "Utilities": "公用事業",
}


def _yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-")     # BRK.B → BRK-B


def _f(v):
    try:
        if v is None:
            return None
        x = float(v)
        return None if x != x else x    # NaN
    except (TypeError, ValueError):
        return None


def _pct(a, b):
    if a is None or b is None or b <= 0:
        return None
    return round((a / b - 1) * 100, 1)


def fetch_quarters(symbol: str, quarters: int = QUARTERS) -> list[dict] | None:
    """近 quarters 季（新→舊）。每季：period(YYYY-MM 季底)、revenue(美元)、revenue_yoy、
    eps(調整後)、eps_estimate、surprise_pct、gross_margin(%)、capex(美元)。"""
    import yfinance as yf

    t = yf.Ticker(_yahoo_symbol(symbol))
    try:
        inc = t.quarterly_income_stmt
    except Exception as e:  # noqa: BLE001
        log.warning("%s 季損益表失敗：%s", symbol, e)
        return None
    if inc is None or inc.empty or "Total Revenue" not in inc.index:
        return None

    cols = sorted(inc.columns, reverse=True)          # 新→舊
    rev = {c: _f(inc.at["Total Revenue", c]) for c in cols}
    gross = ({c: _f(inc.at["Gross Profit", c]) for c in cols}
             if "Gross Profit" in inc.index else {})

    capex: dict = {}
    try:
        cf = t.quarterly_cashflow
        if cf is not None and "Capital Expenditure" in cf.index:
            capex = {c: _f(cf.at["Capital Expenditure", c]) for c in cf.columns}
    except Exception:  # noqa: BLE001
        pass

    # 已公布 EPS：財報公布日 → 對應「公布日前 75 天內最近的季底」
    eps_rows: list[tuple] = []
    try:
        ed = t.earnings_dates
        if ed is not None and not ed.empty:
            rep = ed.dropna(subset=["Reported EPS"])
            for ts, row in rep.iterrows():
                eps_rows.append((ts.date(), _f(row.get("Reported EPS")),
                                 _f(row.get("EPS Estimate")), _f(row.get("Surprise(%)"))))
    except Exception:  # noqa: BLE001
        pass

    def _eps_for(period_end):
        pe = period_end.date() if hasattr(period_end, "date") else period_end
        cands = [r for r in eps_rows if pe < r[0] <= pe + timedelta(days=75)]
        if not cands:
            return None, None, None
        r = min(cands, key=lambda x: x[0])
        return r[1], r[2], r[3]

    out = []
    for i, c in enumerate(cols[:quarters]):
        r = rev.get(c)
        g = gross.get(c)
        eps, est, sur = _eps_for(c)
        # YoY：往後找 4 季（同季去年）；yfinance 只給 5～7 季，最舊幾季算不出來
        ly = rev.get(cols[i + 4]) if i + 4 < len(cols) else None
        pq = rev.get(cols[i + 1]) if i + 1 < len(cols) else None
        cx = capex.get(c)
        out.append({
            "period": c.strftime("%Y-%m"),
            "revenue": r,
            "revenue_yoy": _pct(r, ly),
            "revenue_qoq": _pct(r, pq),
            "eps": round(eps, 2) if eps is not None else None,
            "eps_estimate": round(est, 2) if est is not None else None,
            "surprise_pct": round(sur, 1) if sur is not None else None,
            "gross_margin": round(g / r * 100, 1) if g is not None and r else None,
            "capex": abs(cx) if cx is not None else None,
        })
    return out or None


def fetch_profile(symbol: str) -> dict | None:
    """{sector, sector_zh, industry, pe, market_cap, name_en, summary_en}。"""
    import yfinance as yf

    try:
        info = yf.Ticker(_yahoo_symbol(symbol)).info or {}
    except Exception as e:  # noqa: BLE001
        log.warning("%s info 失敗：%s", symbol, e)
        return None
    sector = info.get("sector") or ""
    return {
        "sector": sector,
        "sector_zh": SECTOR_ZH.get(sector, sector),
        "industry": info.get("industry") or "",
        "pe": _f(info.get("trailingPE")),
        "market_cap": _f(info.get("marketCap")),
        "name_en": info.get("longName") or info.get("shortName") or symbol,
        "summary_en": (info.get("longBusinessSummary") or "")[:4000],
    }


def peer_pe_by_sector(profiles: dict[str, dict], min_samples: int = 4) -> dict[str, dict]:
    """{sector_zh: {median, mean, n}}，樣本＝白名單內同 sector 有本益比者。"""
    buckets: dict[str, list[float]] = {}
    for p in profiles.values():
        if p and p.get("pe") and p["pe"] > 0 and p.get("sector_zh"):
            buckets.setdefault(p["sector_zh"], []).append(p["pe"])
    out = {}
    for sec, xs in buckets.items():
        if len(xs) < min_samples:
            continue
        xs = sorted(xs)
        n = len(xs)
        med = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
        out[sec] = {"median": round(med, 2), "mean": round(sum(xs) / n, 2), "n": n}
    return out


# ── 業務族群（Gemini 讀公司描述） ────────────────────────────

class _Seg(BaseModel):
    name: str
    pct: float | None = None
    note: str = ""


class _Biz(BaseModel):
    segments: list[_Seg] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


_SYSTEM = (
    "你是美股產業分析師。只根據提供的公司描述作答，嚴禁杜撰。"
    "輸出一律繁體中文（台灣用語），不使用 emoji。"
)
_PROMPT = """以下是 {name}（{symbol}，{sector} / {industry}）的公司業務描述：

{summary}

1. 列出這家公司的主要業務／產品線（用繁體中文、每項 12 字內、3～6 項）。
   描述裡若有明確寫出各業務的營收比重才填 pct（0~100），沒有就填 null，不要推測。
2. 從下列固定清單挑 1～4 個最能代表這家公司的族群標籤（只能用清單內的字）：
{taxonomy}

輸出 JSON（不要 markdown code fence）：
{{"segments":[{{"name":"…","pct":null,"note":""}}],"tags":["…"]}}"""


def extract_business(symbol: str, name: str, profile: dict) -> dict:
    """回 {segments, tags}；無描述回空。額度耗盡丟 RuntimeError。"""
    summary = (profile or {}).get("summary_en") or ""
    if not summary:
        return {"segments": [], "tags": []}
    if all_exhausted():
        raise RuntimeError("Gemini 今日額度已耗盡")
    prompt = _PROMPT.format(name=name, symbol=symbol,
                            sector=profile.get("sector", ""),
                            industry=profile.get("industry", ""),
                            summary=summary, taxonomy="、".join(TAXONOMY))
    resp = generate_with_retry(
        [prompt], timeout_ms=120_000,
        config_=types.GenerateContentConfig(
            temperature=0.1, system_instruction=_SYSTEM,
            response_mime_type="application/json", response_schema=_Biz),
    )
    parsed = resp.parsed
    if not isinstance(parsed, _Biz):
        parsed = _Biz.model_validate(json.loads(resp.text))
    tags = [t.strip() for t in parsed.tags if t.strip() in TAXONOMY][:4]
    segs = [{"name": s.name.strip(), "pct": s.pct if s.pct is None or 0 <= s.pct <= 100 else None,
             "note": s.note.strip()} for s in parsed.segments if s.name.strip()]
    log.info("美股族群 %s %s：%d 項、%s（%s）", symbol, name, len(segs),
             "/".join(tags) or "無", getattr(resp, "model_version", ""))
    time.sleep(0.5)
    return {"segments": segs, "tags": tags,
            "model": getattr(resp, "model_version", "") or ""}
