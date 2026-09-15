"""規則可算的市場事件：台指期／選擇權結算、美股選擇權到期、財報與月營收申報截止、指數調整生效。

不用任何 API 或 AI，全部由曆法規則推出，每天建置時重算一次（明年的日期自然會出現）。
唯一的外部相依是「台股休市日」——期交所遇假日把最後交易日順延到次一營業日、
申報截止日遇假日也順延，所以要知道哪天不開盤：從 TWSE OpenAPI 抓當年度休市表，
快取在 site/.tw_holidays.json 逐年累積；還沒公布的年份退回「週末＋固定國定假日」。

事件格式與 macro_upcoming.json 其他事件相同，多兩個欄位：
  kind  = settle（結算／到期）| tw（台股制度：申報截止、指數調整）
  rule  = True（規則事件，建置時整批重算、不保留舊檔裡的）
重要度（impact 1～3）與站上其他事件同一把尺：季結算／FOMC／CPI／非農 3，月結算／三巫／申報截止 2，
週選／月選擇權／指數調整 1。
"""
import json
from datetime import date, timedelta
from pathlib import Path

import requests

TW_FLAG = "\U0001F1F9\U0001F1FC"
US_FLAG = "\U0001F1FA\U0001F1F8"

TWSE_HOLIDAY_API = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"

# 還沒公布休市表的年份用：固定日期的國定假日（農曆假日無法預知，只能等 TWSE 公布）
_FIXED_TW_HOLIDAYS = ((1, 1), (2, 28), (4, 4), (4, 5), (5, 1), (9, 28), (10, 10), (10, 25), (12, 25))


# ---------- 台股休市日 ----------

class TwHolidays:
    def __init__(self, cache_path: Path | None):
        self.cache_path = cache_path
        self.by_year: dict[str, list[str]] = {}
        if cache_path and cache_path.exists():
            try:
                self.by_year = json.loads(cache_path.read_text(encoding="utf-8"))
            except ValueError:
                self.by_year = {}
        self._set = {d for ds in self.by_year.values() for d in ds}

    def refresh(self) -> None:
        """抓 TWSE 當年度休市表（API 只給當年）；成功就寫回快取。"""
        try:
            r = requests.get(TWSE_HOLIDAY_API, headers={"accept": "application/json"}, timeout=20)
            r.raise_for_status()
            rows = r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"  TWSE 休市表抓取失敗（沿用快取 {len(self._set)} 天）：{str(e)[:80]}")
            return
        days: dict[str, list[str]] = {}
        for row in rows:
            name = row.get("Name", "")
            raw = row.get("Date", "")
            # 表內混有「開始交易日／最後交易日」這種其實有開盤的提醒條目，要排除；
            # 「市場無交易，僅辦理結算交割」是不開盤，算休市
            if "交易日" in name or len(raw) != 7 or not raw.isdigit():
                continue
            y = int(raw[:3]) + 1911
            iso = f"{y}-{raw[3:5]}-{raw[5:7]}"
            days.setdefault(str(y), []).append(iso)
        if not days:
            return
        for y, ds in days.items():
            self.by_year[y] = sorted(set(ds))
        self._set = {d for ds in self.by_year.values() for d in ds}
        if self.cache_path:
            self.cache_path.write_text(json.dumps(self.by_year, ensure_ascii=False, indent=0),
                                       encoding="utf-8")
        print(f"  TWSE 休市表：{', '.join(f'{y} 年 {len(ds)} 天' for y, ds in sorted(days.items()))}")

    def has_year(self, y: int) -> bool:
        return str(y) in self.by_year

    def is_closed(self, d: date) -> bool:
        if d.weekday() >= 5:
            return True
        if self.has_year(d.year):
            return d.isoformat() in self._set
        return (d.month, d.day) in _FIXED_TW_HOLIDAYS


# ---------- 美股休市日（NYSE） ----------

def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nth_weekday(y: int, m: int, weekday: int, n: int) -> date:
    """某月第 n 個星期 weekday（Mon=0）。"""
    first = date(y, m, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + 7 * (n - 1))


def last_weekday(y: int, m: int, weekday: int) -> date:
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def nyse_holidays(y: int) -> set[date]:
    return {
        _observed(date(y, 1, 1)), nth_weekday(y, 1, 0, 3), nth_weekday(y, 2, 0, 3),
        _easter(y) - timedelta(days=2), last_weekday(y, 5, 0), _observed(date(y, 6, 19)),
        _observed(date(y, 7, 4)), nth_weekday(y, 9, 0, 1), nth_weekday(y, 11, 3, 4),
        _observed(date(y, 12, 25)),
    }


def _us_closed(d: date) -> bool:
    return d.weekday() >= 5 or d in nyse_holidays(d.year)


# ---------- 事件產生 ----------

def _next_open(d: date, closed) -> date:
    while closed(d):
        d += timedelta(days=1)
    return d


def _prev_open(d: date, closed) -> date:
    while closed(d):
        d -= timedelta(days=1)
    return d


def _ev(d: date, country: str, name: str, impact: int, kind: str, interp: str,
        time: str = "") -> dict:
    return {"date": d.isoformat(), "time": time, "country": country, "name": name,
            "previous": "", "forecast": "", "actual": "", "impact": impact,
            "kind": kind, "interpretation": interp, "rule": True}


def generate(start: date, end: date, tw: TwHolidays) -> list[dict]:
    """產生 [start, end] 內的規則事件（含當月），日期新→舊不排序，由呼叫端合併。"""
    out: list[dict] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        quarter_end = m in (3, 6, 9, 12)

        # 台指期／選擇權：每月第三個週三；週選擇權：其餘週三（遇休市順延次一營業日）
        third_wed = nth_weekday(y, m, 2, 3)
        settle = _next_open(third_wed, tw.is_closed)
        if quarter_end:
            out.append(_ev(settle, TW_FLAG, "台指期／選擇權季結算", 3, "settle",
                           "期貨季月合約與月選擇權同日到期，換倉與結算部位最大，結算前後波動常放大",
                           "13:30"))
        else:
            out.append(_ev(settle, TW_FLAG, "台指期／選擇權月結算", 2, "settle",
                           "台指期近月與月選擇權到期，最後結算價以當日收盤前一段時間的指數平均計算",
                           "13:30"))
        wed = nth_weekday(y, m, 2, 1)
        while wed.month == m:
            if wed != third_wed:
                out.append(_ev(_next_open(wed, tw.is_closed), TW_FLAG, "台指週選擇權到期", 1,
                               "settle", "週選擇權到期日，價平附近的履約價常見拉鋸", "13:30"))
            wed += timedelta(days=7)

        # 美股：每月第三個週五選擇權到期；季月為三巫日（指數期貨、指數選擇權、個股選擇權同日到期）
        third_fri = _prev_open(nth_weekday(y, m, 4, 3), _us_closed)
        if quarter_end:
            out.append(_ev(third_fri, US_FLAG, "美股三巫日（期貨／選擇權同日到期）", 2, "settle",
                           "美股季度到期日，尾盤成交量常暴增；台北時間隔日凌晨收盤"))
        else:
            out.append(_ev(third_fri, US_FLAG, "美股月選擇權到期", 1, "settle",
                           "美股月選擇權到期，尾盤波動放大；台北時間隔日凌晨收盤"))

        # 月營收：每月 10 日前公布上月營收（遇休市順延）
        pm = 12 if m == 1 else m - 1
        out.append(_ev(_next_open(date(y, m, 10), tw.is_closed), TW_FLAG,
                       f"上市櫃 {pm} 月營收公布截止", 2, "tw",
                       "上市櫃公司須在 10 日前公布上月營收，前幾天為營收密集公布期"))

        # 財報申報截止：Q1 5/15、Q2 8/14、Q3 11/14、年報 3/31（法說會旺季跟著這幾天走）
        deadline = {3: (31, "年報／Q4"), 5: (15, "Q1"), 8: (14, "Q2"), 11: (14, "Q3")}.get(m)
        if deadline:
            out.append(_ev(_next_open(date(y, m, deadline[0]), tw.is_closed), TW_FLAG,
                           f"上市櫃 {deadline[1]} 財報申報截止", 2, "tw",
                           "財報申報最後期限，截止前一兩週為財報與法說會密集期"))

        # 指數調整生效：MSCI 2/5/8/11 月最後交易日收盤後；富時 3/6/9/12 月第三個週五收盤後
        if m in (2, 5, 8, 11):
            month_end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
            out.append(_ev(_prev_open(month_end, tw.is_closed), TW_FLAG,
                           "MSCI 季度調整生效（收盤後）", 1, "tw",
                           "MSCI 指數成分與權重調整於收盤後生效，被動資金在尾盤集中進出"))
        if quarter_end:
            out.append(_ev(_prev_open(nth_weekday(y, m, 4, 3), tw.is_closed), TW_FLAG,
                           "富時指數季度調整生效（收盤後）", 1, "tw",
                           "富時台灣指數系列調整於收盤後生效，被動資金在尾盤集中進出"))

        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    return [e for e in out if start.isoformat() <= e["date"] <= end.isoformat()]
