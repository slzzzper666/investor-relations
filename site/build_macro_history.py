"""總經歷史數據行事曆建置：1 月至今「已公布」的監控總經數據。

兩段來源接續：
  2026-06-12 以前  Investing.com（含市場預期值）——已存於既有 macro_history.json，
                   該站現已擋雲端與家用 IP（403），舊資料原樣保留、不再重抓。
  之後            FRED 官方 API（金鑰免費、雲端可達）：以 release/dates 取「實際公布日」，
                   再對應該期的觀測值；FRED 沒有市場預期值，forecast 留空、解讀改比前值。

輸出：site/public/macro_history.json（增量：只補最後一筆之後的）
用法：python site/build_macro_history.py
"""
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
PUBLIC_DIR = BASE_DIR / "public"
OUT = PUBLIC_DIR / "macro_history.json"
sys.path.insert(0, str(ROOT_DIR))

from config import TZ_TAIPEI                       # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env")
except ImportError:
    pass
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FRED_API = "https://api.stlouisfed.org/fred"
US = "🇺🇸"

# 每個 release 對應要輸出的指標：(series_id, 顯示名, 換算方式, 單位, 低於前值為佳, 重要度)
#   換算：level=原值、yoy=年增率%、mom=月增率%、diff_k=較上期變動（千人）
FRED_RELEASE_METRICS = {
    10: [("CPIAUCSL", "CPI 年增率", "yoy", "%", True, 3),
         ("CPIAUCSL", "CPI 月增率", "mom", "%", True, 3),
         ("CPILFESL", "核心 CPI 年增率", "yoy", "%", True, 3)],
    50: [("PAYEMS", "非農就業人口", "diff_k", "K", False, 3),
         ("UNRATE", "失業率", "level", "%", True, 3)],
    46: [("PPIFIS", "PPI 月增率", "mom", "%", True, 2)],
    53: [("A191RL1Q225SBEA", "GDP 季增年率", "level", "%", False, 2)],
    9: [("RSAFS", "零售銷售月增率", "mom", "%", False, 2)],
}
RELEASE_NAMES = {10: "CPI", 50: "就業報告", 46: "PPI", 53: "GDP", 9: "零售銷售"}


def _get(path: str, **params) -> dict:
    r = requests.get(f"{FRED_API}/{path}", timeout=25, params={
        "api_key": FRED_API_KEY, "file_type": "json", **params})
    r.raise_for_status()
    return r.json()


def _observations(sid: str, start: date) -> list[tuple[date, float]]:
    rows = []
    for o in _get("series/observations", series_id=sid, sort_order="asc",
                  observation_start=start.isoformat()).get("observations", []):
        if o.get("value") in (None, "", "."):
            continue
        try:
            rows.append((date.fromisoformat(o["date"]), float(o["value"])))
        except ValueError:
            continue
    return rows


def _release_dates(rid: int, start: date, end: date) -> list[date]:
    rows = _get("release/dates", release_id=rid, sort_order="asc",
                include_release_dates_with_no_data="false",
                realtime_start=start.isoformat(), realtime_end=end.isoformat()
                ).get("release_dates", [])
    return [date.fromisoformat(r["date"]) for r in rows
            if start <= date.fromisoformat(r["date"]) <= end]


def _value(mode: str, obs: list[tuple[date, float]], i: int) -> float | None:
    """第 i 筆觀測依換算方式算出要顯示的數字。"""
    d, v = obs[i]
    if mode == "level":
        return v
    if mode == "mom" and i >= 1:
        return (v / obs[i - 1][1] - 1) * 100
    if mode == "diff_k" and i >= 1:
        return v - obs[i - 1][1]
    if mode == "yoy":
        target = (d.year - 1, d.month)
        prior = next((pv for pd, pv in obs if (pd.year, pd.month) == target), None)
        return (v / prior - 1) * 100 if prior else None
    return None


def _period_end(start: date, months: int) -> date:
    """期別起始日 + months 個月 − 1 天。"""
    y, m = start.year, start.month + months
    while m > 12:
        y, m = y + 1, m - 12
    return date(y, m, 1) - timedelta(days=1)


def _fmt(v: float | None, unit: str) -> str:
    if v is None:
        return ""
    if unit == "K":
        return f"{v:+,.0f}K"
    return f"{v:.1f}%"


def _interpret(name: str, actual: float | None, prev: float | None,
               lower_is_better: bool) -> str:
    if actual is None or prev is None:
        return "已公布（無前值可比較）"
    if abs(actual - prev) < 1e-9:
        return f"{name}與前值持平。"
    better = (actual < prev) if lower_is_better else (actual > prev)
    trend = "回落" if actual < prev else "上升"
    if lower_is_better:
        return (f"{name}{trend}，通膨／就業壓力較前值緩和。" if better
                else f"{name}{trend}，較前值升溫，需留意對政策路徑的影響。")
    return (f"{name}{trend}，動能優於前值。" if better
            else f"{name}{trend}，動能弱於前值。")


def fred_history(since: date, until: date) -> list[dict]:
    """since～until 之間 FRED 各 release 的實際公布紀錄。"""
    items: list[dict] = []
    obs_cache: dict[str, list] = {}
    for rid, metrics in FRED_RELEASE_METRICS.items():
        try:
            rdates = _release_dates(rid, since, until)
        except Exception as e:  # noqa: BLE001
            print(f"  FRED release {rid} 失敗：{e}")
            continue
        n = 0
        for rd in rdates:
            mo, dd = rd.month, rd.day
            edt = (3, 8) <= (mo, dd) < (11, 1)        # 8:30 ET → 台北 20:30/21:30
            for sid, name, mode, unit, lower_better, impact in metrics:
                if sid not in obs_cache:
                    try:
                        obs_cache[sid] = _observations(sid, since - timedelta(days=500))
                    except Exception as e:  # noqa: BLE001
                        print(f"  FRED {sid} 失敗：{e}")
                        obs_cache[sid] = []
                obs = obs_cache[sid]
                # FRED 的觀測日期是期別「起始日」（2026-08-01 ＝ 8 月整月），
                # 8 月數據要 9 月才公布，所以要用期別「結束日」< 公布日來挑，
                # 否則會把還沒發生的那期當成本次公布（GDP 為季資料，期長 3 個月）。
                months = 3 if rid == 53 else 1
                idx = max((i for i, (d, _) in enumerate(obs)
                           if _period_end(d, months) < rd), default=None)
                if idx is None:
                    continue
                cur = _value(mode, obs, idx)
                prev = _value(mode, obs, idx - 1) if idx >= 1 else None
                period = obs[idx][0]
                label = (f"{name}（{period.year}Q{(period.month - 1) // 3 + 1}）"
                         if rid == 53 else f"{name}（{period.month}月）")
                items.append({
                    "date": rd.isoformat(),
                    "time": "20:30" if edt else "21:30",
                    "country": US,
                    "name": label,
                    "previous": _fmt(prev, unit),
                    "forecast": "",                   # FRED 無市場預期
                    "actual": _fmt(cur, unit),
                    "impact": impact,
                    "interpretation": _interpret(name, cur, prev, lower_better),
                })
                n += 1
        print(f"  FRED {RELEASE_NAMES.get(rid, rid)}：{len(rdates)} 次公布、{n} 筆指標")
    return items


def main() -> None:
    now = datetime.now(TZ_TAIPEI)
    today = now.date()
    if not FRED_API_KEY:
        raise SystemExit("缺少 FRED_API_KEY（.env）")

    existing: list[dict] = []
    if OUT.exists():
        try:
            existing = json.loads(OUT.read_text(encoding="utf-8")).get("items", [])
        except ValueError:
            existing = []
    last = max((it["date"] for it in existing), default="2025-12-31")
    since = date.fromisoformat(last) + timedelta(days=1)
    print(f"===== 總經歷史：既有 {len(existing)} 筆（至 {last}），FRED 接續 {since} ~ {today} =====")

    new = fred_history(since, today)
    seen = {(it["date"], it["country"], it["name"]) for it in existing}
    added = [it for it in new if (it["date"], it["country"], it["name"]) not in seen]
    items = existing + added
    items.sort(key=lambda x: (x["date"], x["time"]))

    OUT.write_text(json.dumps({"generated_at": now.strftime("%Y-%m-%d %H:%M"),
                               "count": len(items), "items": items},
                              ensure_ascii=False), encoding="utf-8")
    print(f"已寫入 {OUT}（{OUT.stat().st_size:,} bytes）：共 {len(items)} 筆、新增 {len(added)} 筆")
    from collections import Counter
    c = Counter(it["date"][:7] for it in items)
    for mk in sorted(c):
        print(f"    {mk}: {c[mk]} 筆")


if __name__ == "__main__":
    main()
