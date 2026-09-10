"""近 N 季財報序列（成長性判讀用）。

資料源：FinMind 開放 API（免金鑰）
  TaiwanStockFinancialStatements  綜合損益表——**已是單季值**，不需累計相減
  TaiwanStockCashFlowsStatement   現金流量表——**年度累計值**，須相減得單季

為什麼不用 MOPS：t163sb01 是累計值，單季要兩次呼叫；近 6 季就得打十幾次，
而 MOPS 對密集請求回 406（雲端與本機皆曾被擋）。FinMind 一檔兩次請求就拿到
完整歷史，是這個需求唯一撐得住的來源。
"""
import time
from datetime import date

import requests

from ir.logger import get_logger

log = get_logger("ir.fin6q")

API = "https://api.finmindtrade.com/api/v4/data"
QUARTERS = 6          # 對外提供幾季
_FETCH_QUARTERS = 9   # 多抓幾季：算 YoY 與現金流相減都需要更早的季

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": "Mozilla/5.0"})
    return _session


class RateLimited(Exception):
    """FinMind 免費額度用盡（每小時上限），呼叫端應停手等下一輪。"""


def _query(dataset: str, code: str, start: str) -> list[dict]:
    for attempt in range(3):
        try:
            r = _get_session().get(API, params={
                "dataset": dataset, "data_id": code, "start_date": start,
            }, timeout=45)
        except requests.RequestException as e:
            log.warning("FinMind %s %s 連線失敗（第 %d 次）：%s",
                        dataset, code, attempt + 1, e)
            time.sleep(3)
            continue
        if r.status_code in (401, 402, 429):
            raise RateLimited(f"FinMind 額度／限流：HTTP {r.status_code}")
        if r.status_code != 200:
            log.warning("FinMind %s %s 回 HTTP %s", dataset, code, r.status_code)
            time.sleep(2)
            continue
        try:
            return r.json().get("data") or []
        except ValueError:
            log.warning("FinMind %s %s 回應非 JSON", dataset, code)
            time.sleep(2)
    return []


def _q_label(d: str) -> str:
    """'2026-06-30' → '2026 Q2'。"""
    y, m, _ = d.split("-")
    return f"{y} Q{(int(m) - 1) // 3 + 1}"


def _prev_quarter_date(d: str) -> str:
    """'2026-06-30' → '2026-03-31'（上一季的季底日）。"""
    y, m = int(d[:4]), int(d[5:7])
    q = (m - 1) // 3 + 1
    if q == 1:
        y, q = y - 1, 4
    else:
        q -= 1
    return f"{y}-{['03-31', '06-30', '09-30', '12-31'][q - 1]}"


def _pct(a, b):
    """年／季增率。基期為零或負數時回 None。

    去年虧損、今年轉盈的情況算不出有意義的百分比（-0.5 → +0.3 會算成 -160%，
    看起來像衰退但其實是轉虧為盈），這種情形不給數字，讓 EPS 原始值自己說話。
    """
    if a is None or b is None or b <= 0:
        return None
    return round((a / b - 1) * 100, 1)


def fetch(code: str, quarters: int = QUARTERS) -> list[dict] | None:
    """回近 quarters 季（新→舊）。每季：

      period, revenue(億), eps, gross_margin(%), capex(億),
      revenue_yoy, revenue_qoq, eps_yoy

    查無資料回 None；額度用盡丟 RateLimited。
    """
    start = date(date.today().year - 3, 1, 1).isoformat()
    rows = _query("TaiwanStockFinancialStatements", code, start)
    if not rows:
        return None

    # {季底日: {科目: 值}}；FinMind 損益表已是單季值
    by_q: dict[str, dict[str, float]] = {}
    for r in rows:
        v = r.get("value")
        if v is None:
            continue
        by_q.setdefault(r["date"], {})[r["type"]] = v

    # 現金流量表是年度累計 → 相減成單季
    capex_cum: dict[str, float] = {}
    for r in _query("TaiwanStockCashFlowsStatement", code, start):
        if r.get("type") == "PropertyAndPlantAndEquipment" and r.get("value") is not None:
            capex_cum[r["date"]] = r["value"]

    def capex_single(qd: str):
        cur = capex_cum.get(qd)
        if cur is None:
            return None
        if qd[5:7] == "03":          # Q1 的累計就是單季
            return abs(cur)
        prev = capex_cum.get(_prev_quarter_date(qd))
        return None if prev is None else abs(cur - prev)

    dates = sorted(by_q, reverse=True)[:_FETCH_QUARTERS]
    out: list[dict] = []
    for qd in dates[:quarters]:
        cur = by_q[qd]
        rev, eps = cur.get("Revenue"), cur.get("EPS")
        gross = cur.get("GrossProfit")
        ly = by_q.get(f"{int(qd[:4]) - 1}{qd[4:]}", {})
        pq = by_q.get(_prev_quarter_date(qd), {})
        cx = capex_single(qd)
        out.append({
            "period": _q_label(qd),
            "revenue": round(rev / 1e8, 2) if rev is not None else None,   # 元→億
            "eps": round(eps, 2) if eps is not None else None,
            "gross_margin": (round(gross / rev * 100, 1)
                             if gross is not None and rev else None),
            "capex": round(cx / 1e8, 2) if cx is not None else None,       # 元→億
            "revenue_yoy": _pct(rev, ly.get("Revenue")),
            "revenue_qoq": _pct(rev, pq.get("Revenue")),
            "eps_yoy": _pct(eps, ly.get("EPS")),
        })
    return out or None
