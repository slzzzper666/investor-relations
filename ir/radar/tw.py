"""台股財報模組。

台股無官方前瞻財報行事曆，採組合方案（詳見 research/tw_findings.md）：
- 行事曆：法定截止日規則 + MOPS 法說會行事曆（ajax_t100sb02_1，唯一官方前瞻日程）。
- 結果：MOPS 重大訊息（t05st02/t05sr01_1）偵測「董事會通過財務報告」
  → t163sb01 查簡明損益表（累計值，單季 = 累計相減）→ EPS/營收/年增/季增。
- 市場預期：台股無全市場免費預期值來源 → 以「去年同期」為比較基準。
注意：MOPS 數值為民國年、新台幣仟元；t163sb01 為累計值。
"""
import re
import time
from datetime import date, timedelta

import requests

from ir.logger import get_logger
from ir.mops import get_conferences_in_range
from ir.net import get_session
from ir.radar.models import TwEarning

log = get_logger("ir.radar.tw")

MOPS_API = "https://mops.twse.com.tw/mops/api"
MOPS_HEADERS = {  # User-Agent 由共用 session 提供
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://mops.twse.com.tw",
    "Referer": "https://mops.twse.com.tw/mops/",
}
ROC_BASE = 1911
MAX_RESULT_COMPANIES = 15  # 每輪最多處理幾家（截止日尖峰時其餘留待下一輪）

# 董事會通過財報的主旨樣式（排除「召開日期預告」「自結」等雜訊）
REPORT_RE = re.compile(r"董事會(?:決議)?通過.{0,20}財務報告")
# 主旨中的報告年季："115年第一季" / "115年第1季" / "114年度"
PERIOD_RE = re.compile(r"(\d{2,3})年(?:第([一二三四1234])季|度)")
SEASON_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}

# 法定截止日（月, 日, 標籤）
QUARTER_DEADLINES = [
    (3, 31, "年報公布截止"),
    (5, 15, "Q1 財報截止（金控至 5/30）"),
    (8, 14, "Q2 財報截止（金控至 8/31）"),
    (11, 14, "Q3 財報截止（金控至 11/29）"),
]

_ccsi_cache: dict[tuple, dict] = {}


def _mops_json(api: str, payload: dict) -> dict:
    """POST 新版 MOPS JSON API。HTTP 200 但 body code!=200 也視為失敗。"""
    for attempt in range(3):
        try:
            r = get_session().post(
                f"{MOPS_API}/{api}", json=payload, headers=MOPS_HEADERS, timeout=30
            )
            r.raise_for_status()
            j = r.json()
            if j.get("code") != 200:
                raise ValueError(f"MOPS {api} 回應 code={j.get('code')}")
            return j.get("result") or {}
        except (requests.RequestException, ValueError) as e:
            log.warning("MOPS %s 第 %d 次失敗：%s", api, attempt + 1, e)
            time.sleep(2)
    return {}


def _roc_to_iso(roc: str) -> str | None:
    """'115/06/15' 或 '1150615' → '2026-06-15'。"""
    m = re.match(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", roc.strip()) or re.match(
        r"(\d{3})(\d{2})(\d{2})$", roc.strip()
    )
    if not m:
        return None
    y, mo, d = int(m.group(1)) + ROC_BASE, int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


# ── 行事曆 ────────────────────────────────────────────────


def _deadline_events(start: date, end: date) -> list[TwEarning]:
    events = []
    for year in {start.year, end.year}:
        for mo, day, label in QUARTER_DEADLINES:
            try:
                d = date(year, mo, day)
            except ValueError:
                continue
            if start <= d <= end:
                events.append(TwEarning("🗓", label, d.isoformat()))
        for mo in range(1, 13):
            d = date(year, mo, 10)
            if start <= d <= end:
                prev = mo - 1 if mo > 1 else 12
                events.append(
                    TwEarning("🗓", f"{prev}月營收公布截止（全市場）", d.isoformat())
                )
    return events


def _conference_events(start: date, end: date) -> list[TwEarning]:
    """法說會行事曆：複用 IR 既有的 MOPS 法說會抓取（消除重複）。"""
    events = []
    for c in get_conferences_in_range(start, end):
        t = c.time if re.match(r"\d{1,2}:\d{2}", c.time or "") else ""
        events.append(
            TwEarning(c.stock_code, c.company_name, c.date.isoformat(),
                      time=t, note="法說會")
        )
    return events


def fetch_calendar(start: date, end: date) -> list[TwEarning]:
    """法定截止日 + 法說會行事曆（法說會資料來自 ir.mops）。"""
    events = _deadline_events(start, end)
    events.extend(_conference_events(start, end))
    log.info("台股行事曆：%d 筆", len(events))
    return events


# ── 財報結果 ──────────────────────────────────────────────


def _scan_announcements(days: list[date]) -> list[dict]:
    """掃描重大訊息找「董事會通過財務報告」（上市/上櫃）。"""
    found: dict[tuple, dict] = {}

    def _add(code: str, name: str, ann_date: str, subject: str, market: str):
        if market not in ("sii", "otc"):
            return
        subject = re.sub(r"\s+", "", subject)
        if not REPORT_RE.search(subject):
            return
        m = PERIOD_RE.search(subject)
        if not m:
            return
        year = int(m.group(1))
        season = SEASON_MAP[m.group(2)] if m.group(2) else 4  # 「年度」= Q4
        key = (code, year, season)
        if key not in found:
            found[key] = {
                "code": code, "name": name, "date": ann_date,
                "year": year, "season": season,
            }

    # 指定日全市場（含回補）
    for d in days:
        result = _mops_json(
            "t05st02",
            {"year": str(d.year - ROC_BASE), "month": str(d.month), "day": str(d.day)},
        )
        for row in result.get("data") or []:
            # 欄序：日期, 時間, 代號, 名稱, 主旨, 詳細參數(dict 含 marketKind)
            if len(row) < 6 or not isinstance(row[5], dict):
                continue
            market = (row[5].get("parameters") or {}).get("marketKind", "")
            iso = _roc_to_iso(str(row[0])) or d.isoformat()
            _add(str(row[2]), str(row[3]), iso, str(row[4]), market)
        time.sleep(1)

    # 即時清單
    result = _mops_json("home_page/t05sr01_1", {"count": "0", "marketKind": ""})
    for item in result.get("data") or []:
        if not isinstance(item, dict):
            continue
        market = ((item.get("url") or {}).get("parameters") or {}).get(
            "marketKind", ""
        ) or item.get("marketKind", "")
        iso = _roc_to_iso(str(item.get("date", ""))) or date.today().isoformat()
        _add(
            str(item.get("companyId", "")),
            str(item.get("companyAbbreviation", "")),
            iso,
            str(item.get("subject", "")),
            market,
        )
    return list(found.values())


def _parse_num(s: str) -> float | None:
    t = str(s).replace(",", "").strip()
    if not t or t in ("-", "--"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _ccsi(code: str, year: int, season: int) -> dict:
    """t163sb01 簡明損益表（累計值）。回 {'revenue': 仟元, 'eps': 元} 或空 dict。"""
    key = (code, year, season)
    if key in _ccsi_cache:
        return _ccsi_cache[key]
    result = _mops_json(
        "t163sb01",
        {"companyId": code, "dataType": "2", "year": str(year),
         "season": str(season), "subsidiaryCompanyId": ""},
    )
    time.sleep(1)
    out: dict = {}
    rows = {str(r[0]): r[1] for r in (result.get("CCSI") or {}).get("data") or [] if r}
    # top line 科目依業別不同：一般業=營業收入、金控/銀行=淨收益、證券=收益
    for item in ("營業收入", "淨收益", "收益", "收入"):
        if item in rows:
            out["revenue"] = _parse_num(rows[item])
            break
    for item, val in rows.items():
        if "基本每股盈餘" in item:
            out["eps"] = _parse_num(val)
            break
    _ccsi_cache[key] = out
    return out


def _single_quarter(code: str, year: int, season: int) -> tuple[float | None, float | None]:
    """單季 (營收仟元, EPS)。Q1 即累計；Q2-Q4 = 累計(n) − 累計(n-1)。"""
    cur = _ccsi(code, year, season)
    if not cur:
        return None, None
    if season == 1:
        return cur.get("revenue"), cur.get("eps")
    prev = _ccsi(code, year, season - 1)
    rev = eps = None
    if cur.get("revenue") is not None and prev.get("revenue") is not None:
        rev = cur["revenue"] - prev["revenue"]
    if cur.get("eps") is not None and prev.get("eps") is not None:
        eps = round(cur["eps"] - prev["eps"], 2)
    return rev, eps


def _pct_change(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b <= 0:
        return None
    return (a / b - 1) * 100


def _fill_metrics(ev: TwEarning, year: int, season: int) -> None:
    rev, eps = _single_quarter(ev.code, year, season)
    if rev is not None:
        ev.revenue = rev * 1000 / 1e8  # 仟元 → 億元
    ev.eps_actual = eps

    # 去年同期（YoY 基準，台股無市場預期值）
    rev_ly, eps_ly = _single_quarter(ev.code, year - 1, season)
    ev.eps_last_year = eps_ly
    ev.eps_yoy = _pct_change(eps, eps_ly)
    ev.revenue_yoy = _pct_change(rev, rev_ly)

    # 季增：上一季（Q1 的上一季 = 去年 Q4）
    if season == 1:
        rev_pq, _ = _single_quarter(ev.code, year - 1, 4)
    else:
        rev_pq, _ = _single_quarter(ev.code, year, season - 1)
    ev.revenue_qoq = _pct_change(rev, rev_pq)


def fetch_results(scan_days: list[date] | None = None) -> list[TwEarning]:
    """偵測剛公布的財報並抓取數據（比較基準＝去年同期）。"""
    today = date.today()
    days = scan_days or [today - timedelta(days=1), today]
    anns = _scan_announcements(days)
    log.info("台股財報公告：%d 家", len(anns))
    results = []
    for ann in anns[:MAX_RESULT_COMPANIES]:
        ev = TwEarning(ann["code"], ann["name"], ann["date"])
        try:
            _fill_metrics(ev, ann["year"], ann["season"])
        except Exception as e:
            log.warning("%s 財報數據抓取失敗：%s", ann["code"], e)
        if ev.eps_actual is not None or ev.revenue is not None:
            results.append(ev)
        else:
            log.info("%s %s 公告已偵測但數據未入庫，下輪再試", ann["code"], ann["name"])
    log.info("台股財報結果：%d 家", len(results))
    return results
