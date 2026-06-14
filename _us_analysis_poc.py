"""POC：抓美股財報數據 → 用 IR 的 Gemini 引擎做 AI 分析。

驗證「財報雷達抓數據 × IR 的 AI 分析能力」整合後的可能性。
"""
import time

import pandas as pd
import yfinance as yf
from google import genai
from google.genai import types
from pydantic import BaseModel

import config
from ir.gemini_util import generate_with_retry
from ir.logger import get_logger

log = get_logger("poc.us_analysis")


# ── 1. 抓財報數據（yfinance）──────────────────────────────


def fetch_earnings(symbol: str) -> dict | None:
    t = yf.Ticker(symbol)
    try:
        ed = t.earnings_dates
    except Exception as e:
        log.warning("%s earnings_dates 失敗：%s", symbol, e)
        return None
    if ed is None or ed.empty:
        return None
    reported = ed.dropna(subset=["Reported EPS"]).sort_index()
    if reported.empty:
        return None
    row = reported.iloc[-1]

    info = {}
    try:
        info = t.info
    except Exception:
        pass

    data = {
        "symbol": symbol,
        "name": info.get("shortName") or symbol,
        "sector": info.get("sector", ""),
        "report_date": reported.index[-1].date().isoformat(),
        "eps_actual": _f(row.get("Reported EPS")),
        "eps_estimate": _f(row.get("EPS Estimate")),
        "surprise_pct": _f(row.get("Surprise(%)")),
    }

    # 營收（季）+ YoY
    try:
        inc = t.quarterly_income_stmt
        if inc is not None and "Total Revenue" in inc.index:
            revs = inc.loc["Total Revenue"].dropna()
            if len(revs) >= 1:
                data["revenue"] = _f(revs.iloc[0])
                if len(revs) >= 5 and revs.iloc[4]:
                    data["revenue_yoy"] = (revs.iloc[0] / revs.iloc[4] - 1) * 100
    except Exception:
        pass

    # 近期股價反應
    for k in ("postMarketChangePercent", "regularMarketChangePercent"):
        if info.get(k) is not None:
            data["price_reaction_pct"] = _f(info[k])
            break
    data["pe"] = _f(info.get("trailingPE"))
    return data


def _f(v):
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _usd(v):
    if v is None:
        return "N/A"
    if abs(v) >= 1e9:
        return f"${v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v:,.0f}"


# ── 2. AI 分析（複用 IR 的 Gemini 降級鏈）─────────────────

_SYSTEM = (
    "你是資深美股財報分析師，擅長從季度財報數據解讀對股價與產業的意涵。"
    "輸出一律使用繁體中文（台灣用語），語氣專業精煉、客觀，不要出現任何 emoji。"
)

_PROMPT = """以下是 {name}（{symbol}）最近一季財報的關鍵數據，請做分析。

財報日期：{report_date}
產業：{sector}
EPS 實際：{eps_actual}　市場預期：{eps_estimate}　差異：{surprise}
營收：{revenue}　營收年增率：{rev_yoy}
公布後股價反應：{price}　本益比(TTM)：{pe}

請輸出 JSON（不要 markdown code fence），格式：
{{
  "one_liner": "一句話總結這份財報（60字內）",
  "beat_or_miss": "超預期 / 符合預期 / 低於預期 其中之一，並用一句話說明關鍵",
  "highlights": ["3~5 條財報亮點或值得注意的數字解讀"],
  "risks": ["2~3 條潛在隱憂或需追蹤的風險"],
  "ai_view": "你的觀點：這份財報透露的訊號、對股價的短中期意涵、後續追蹤指標（200字內）"
}}"""


class USEarningsAnalysis(BaseModel):
    one_liner: str
    beat_or_miss: str
    highlights: list[str]
    risks: list[str]
    ai_view: str


def _fmt(v, suffix="", money=False):
    if v is None:
        return "N/A"
    if money:
        return _usd(v)
    return f"{v:.2f}{suffix}"


def analyze(data: dict) -> dict:
    prompt = _PROMPT.format(
        name=data["name"], symbol=data["symbol"],
        report_date=data["report_date"], sector=data.get("sector") or "—",
        eps_actual=_fmt(data.get("eps_actual")),
        eps_estimate=_fmt(data.get("eps_estimate")),
        surprise=_fmt(data.get("surprise_pct"), "%"),
        revenue=_fmt(data.get("revenue"), money=True),
        rev_yoy=_fmt(data.get("revenue_yoy"), "%"),
        price=_fmt(data.get("price_reaction_pct"), "%"),
        pe=_fmt(data.get("pe")),
    )
    client = genai.Client(api_key=config.GEMINI_API_KEY,
                          http_options=types.HttpOptions(timeout=120_000))
    resp = generate_with_retry(
        client,
        contents=[prompt],
        config=types.GenerateContentConfig(
            temperature=0.3,
            system_instruction=_SYSTEM,
            response_mime_type="application/json",
            response_schema=USEarningsAnalysis,
        ),
    )
    return resp.parsed.model_dump()


# ── 3. 跑 POC ─────────────────────────────────────────────

if __name__ == "__main__":
    symbols = ["ORCL", "ADBE"]  # 近期剛公布財報的大型股
    for sym in symbols:
        print("=" * 50)
        log.info("抓取 %s 財報數據…", sym)
        data = fetch_earnings(sym)
        if not data:
            log.warning("%s 無財報數據，略過", sym)
            continue
        print(f"📊 {data['name']}（{sym}）財報數據")
        print(f"   日期：{data['report_date']}")
        print(f"   EPS：實際 {_fmt(data.get('eps_actual'))} / "
              f"預期 {_fmt(data.get('eps_estimate'))} / "
              f"surprise {_fmt(data.get('surprise_pct'), '%')}")
        print(f"   營收：{_fmt(data.get('revenue'), money=True)} "
              f"(YoY {_fmt(data.get('revenue_yoy'), '%')})")
        print(f"   股價反應：{_fmt(data.get('price_reaction_pct'), '%')} / "
              f"PE {_fmt(data.get('pe'))}")

        log.info("送 Gemini 分析…")
        try:
            a = analyze(data)
        except Exception as e:
            log.error("%s AI 分析失敗：%s", sym, e)
            continue
        print(f"\n🤖 AI 分析")
        print(f"   一句話：{a['one_liner']}")
        print(f"   判定：{a['beat_or_miss']}")
        print(f"   亮點：")
        for h in a["highlights"]:
            print(f"     • {h}")
        print(f"   隱憂：")
        for r in a["risks"]:
            print(f"     • {r}")
        print(f"   AI 觀點：{a['ai_view']}")
        print()
        time.sleep(1)
