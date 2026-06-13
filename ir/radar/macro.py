"""總經數據模組。

主來源：Investing.com 內部 POST API getCalendarFilteredData
（行事曆 + 前值 + 預期 + 實際，美國/台灣，timeZone=28 = GMT+8 牆鐘時間）。
單頁上限 200 筆，依 bind_scroll_handler 分頁。詳見 research/macro_findings.md。
"""
import re
import time
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from config import TZ_TAIPEI
from ir.logger import get_logger
from ir.net import get_session
from ir.radar.models import MacroEvent

log = get_logger("ir.radar.macro")

INVESTING_URL = (
    "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
)
INVESTING_HEADERS = {  # User-Agent 由共用 session 提供
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.investing.com/economic-calendar/",
}
COUNTRY_US = "5"
COUNTRY_TW = "46"
MAX_PAGES = 10

# ── 監控指標定義 ──────────────────────────────────────────
# (國家, 名稱 regex, 中文基底名, lower_is_better, 解讀文案家族)
TARGETS = [
    ("United States", r"^Core CPI \(", "核心CPI", True, "inflation"),
    ("United States", r"^CPI \(", "CPI", True, "inflation"),
    ("United States", r"^Core PPI \(", "核心PPI", True, "inflation"),
    ("United States", r"^PPI \(", "PPI", True, "inflation"),
    ("United States", r"^Fed Interest Rate Decision", "聯準會利率決策(FOMC)", True, "rate"),
    ("United States", r"^Nonfarm Payrolls", "非農就業人口", False, "jobs"),
    ("United States", r"^Unemployment Rate", "失業率", True, "unemployment"),
    ("United States", r"^GDP \(", "GDP", False, "growth"),
    ("United States", r"^Core Retail Sales \(", "核心零售銷售", False, "growth"),
    ("United States", r"^Retail Sales \(", "零售銷售", False, "growth"),
    ("United States", r"ISM Manufacturing PMI", "ISM製造業PMI", False, "pmi"),
    ("United States", r"ISM Non-Manufacturing PMI|ISM Services PMI", "ISM服務業PMI", False, "pmi"),
    ("United States", r"Michigan Consumer Sentiment", "密大消費者信心", False, "sentiment"),
    ("Taiwan", r"Interest Rate Decision", "央行利率決策", True, "rate"),
    ("Taiwan", r"^CPI", "CPI", True, "inflation"),
    ("Taiwan", r"^Exports", "出口", False, "trade"),
    ("Taiwan", r"^Imports", "進口", False, "trade"),
    ("Taiwan", r"^Trade Balance", "貿易帳", False, "trade"),
    ("Taiwan", r"Export Orders", "外銷訂單", False, "trade"),
]

FLAGS = {"United States": "🇺🇸", "Taiwan": "🇹🇼"}
QUALIFIERS = {"MoM": "月增率", "YoY": "年增率", "QoQ": "季增率"}
MONTHS = {
    "Jan": "1月", "Feb": "2月", "Mar": "3月", "Apr": "4月", "May": "5月",
    "Jun": "6月", "Jul": "7月", "Aug": "8月", "Sep": "9月", "Oct": "10月",
    "Nov": "11月", "Dec": "12月",
}

INTERPRET = {
    ("inflation", "better"): "通膨低於預期，降息預期升溫，短期對股市偏多。",
    ("inflation", "worse"): "通膨高於預期，降息預期降低，短期對股市偏空。",
    ("rate", "better"): "利率低於預期，貨幣政策偏鴿，對股市偏多。",
    ("rate", "worse"): "利率高於預期，貨幣政策偏鷹，對股市偏空。",
    ("rate", "equal"): "利率符合預期，後續關注會後聲明與利率展望。",
    ("jobs", "better"): "就業優於預期，經濟韌性強，但可能壓抑降息預期。",
    ("jobs", "worse"): "就業弱於預期，景氣放緩疑慮升溫，降息預期升高。",
    ("unemployment", "better"): "失業率低於預期，勞動市場穩健。",
    ("unemployment", "worse"): "失業率高於預期，勞動市場降溫。",
    ("growth", "better"): "數據優於預期，經濟動能佳，對股市偏多。",
    ("growth", "worse"): "數據不如預期，經濟動能轉弱，對股市偏空。",
    ("pmi", "better"): "景氣擴張優於預期，對股市偏多。",
    ("pmi", "worse"): "景氣動能不如預期，對股市偏空。",
    ("sentiment", "better"): "消費者信心優於預期，內需動能穩健。",
    ("sentiment", "worse"): "消費者信心轉弱，內需承壓。",
    ("trade", "better"): "外貿數據優於預期，出口動能強，對台股偏多。",
    ("trade", "worse"): "外貿數據不如預期，外需轉弱。",
}


# ── Investing.com 抓取與解析 ──────────────────────────────


def _fetch_raw_rows(start: date, end: date) -> str:
    """抓取區間內全部事件列 HTML（自動分頁）。"""
    session = get_session()
    html_parts = []
    for page in range(MAX_PAGES):
        data = {
            "country[]": [COUNTRY_US, COUNTRY_TW],
            "dateFrom": start.isoformat(),
            "dateTo": end.isoformat(),
            "timeZone": "28",
            "timeFilter": "timeOnly",
            "currentTab": "custom",
            "limit_from": str(page),
        }
        for attempt in range(3):
            try:
                r = session.post(
                    INVESTING_URL, data=data, headers=INVESTING_HEADERS, timeout=20
                )
                r.raise_for_status()
                j = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                log.warning("Investing 第 %d 頁第 %d 次失敗：%s", page, attempt + 1, e)
                time.sleep(3)
        else:
            break
        html_parts.append(j.get("data") or "")
        if not j.get("bind_scroll_handler"):
            break
        time.sleep(1)
    return "".join(html_parts)


def _zh_name(raw_name: str, zh_base: str) -> str:
    """'CPI (MoM)  (May)' → 'CPI 月增率（5月）'。"""
    quals, period = [], ""
    for q in re.findall(r"\(([^)]+)\)", raw_name):
        q = q.strip()
        if q in QUALIFIERS:
            quals.append(QUALIFIERS[q])
        elif q in MONTHS:
            period = f"（{MONTHS[q]}）"
        elif re.fullmatch(r"Q[1-4]", q):
            period = f"（{q}）"
    name = zh_base
    if quals:
        name += f" {'/'.join(quals)}"
    return name + period


def _clean(text: str) -> str:
    return text.replace("\xa0", "").strip()


def _parse_events(rows_html: str) -> list[MacroEvent]:
    soup = BeautifulSoup(rows_html, "html.parser")
    events = []
    for tr in soup.select("tr[id^=eventRowId_]"):
        dt_raw = tr.get("data-event-datetime") or ""
        if not dt_raw:
            continue  # 全天事件（假日等）
        flag = tr.select_one("span.ceFlags")
        country = (flag.get("title") or "").strip() if flag else ""
        name_td = tr.select_one("td.event")
        raw_name = _clean(name_td.get_text()) if name_td else ""
        if not country or not raw_name:
            continue

        matched = None
        for c, pattern, zh_base, lower_better, family in TARGETS:
            if c == country and re.search(pattern, raw_name):
                matched = (zh_base, lower_better, family)
                break
        if not matched:
            continue
        zh_base, lower_better, family = matched
        # 美國同一發布常拆多列（YoY/ex-變體多為 ★1），只留 ★2 以上避免洗版
        impact = len(tr.select("i.grayFullBullishIcon"))
        if country == "United States" and impact < 2:
            continue

        def _cell(prefix: str) -> str:
            td = tr.select_one(f"td[id^={prefix}]")
            return _clean(td.get_text()) if td else ""

        try:
            dt = datetime.strptime(dt_raw, "%Y/%m/%d %H:%M:%S").replace(
                tzinfo=TZ_TAIPEI
            )
        except ValueError:
            continue

        ev = MacroEvent(
            country=FLAGS.get(country, country),
            name=_zh_name(raw_name, zh_base),
            dt_taipei=dt,
            previous=_cell("eventPrevious_"),
            forecast=_cell("eventForecast_"),
            actual=_cell("eventActual_"),
            lower_is_better=lower_better,
            impact=impact,
            source_id=tr.get("id", ""),
        )
        ev.family = family  # 解讀文案用（動態屬性）
        events.append(ev)
    return events


# ── 對外介面 ──────────────────────────────────────────────


def fetch_calendar(start: date, end: date) -> list[MacroEvent]:
    """區間內監控指標的行事曆（不含已公布事件的結果判讀）。"""
    events = _parse_events(_fetch_raw_rows(start, end))
    now = datetime.now(TZ_TAIPEI)
    upcoming = [e for e in events if e.dt_taipei >= now - timedelta(hours=1)]
    log.info("總經行事曆：%d 筆（原始 %d 筆）", len(upcoming), len(events))
    return upcoming


def fetch_results() -> list[MacroEvent]:
    """過去 36 小時內已公布實際值的監控指標。"""
    now = datetime.now(TZ_TAIPEI)
    start = (now - timedelta(hours=36)).date()
    events = _parse_events(_fetch_raw_rows(start, now.date()))
    published = [
        e for e in events
        if e.actual and now - timedelta(hours=36) <= e.dt_taipei <= now
    ]
    for e in published:
        e.interpretation = _interpret(e)
    log.info("總經已公布結果：%d 筆", len(published))
    return published


def _interpret(e: MacroEvent) -> str:
    from ir.radar.formatter import _parse_val

    a = _parse_val(e.actual)
    baseline = _parse_val(e.forecast) or _parse_val(e.previous)
    if a is None or baseline is None:
        return ""
    family = getattr(e, "family", "")
    if a == baseline:
        return INTERPRET.get((family, "equal"), "")
    better = (a < baseline) if e.lower_is_better else (a > baseline)
    return INTERPRET.get((family, "better" if better else "worse"), "")
