"""總經歷史數據行事曆建置：1月至今「已公布」的監控總經數據。

逐月抓 Investing.com（避免單次 2000 列上限），保留有實際值(actual)的事件，
附前值/預期/實際 + 多空解讀，輸出供前端總經行事曆往回翻到 1 月。

Investing.com 擋雲端資料中心 IP → 本機（家用 IP）建好後提交；
本檔為「歷史」（過去不變），只需偶爾重建。

輸出：site/public/macro_history.json
用法：.venv\\Scripts\\python site\\build_macro_history.py
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
PUBLIC_DIR = BASE_DIR / "public"
sys.path.insert(0, str(ROOT_DIR))

from config import TZ_TAIPEI                       # noqa: E402
from ir.radar import macro as M                    # noqa: E402

HISTORY_START = date(2026, 1, 1)


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        first = date(y, m, 1)
        nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        last = min(nxt - timedelta(days=1), end)
        yield first, last
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def main() -> None:
    today = datetime.now(TZ_TAIPEI).date()
    now = datetime.now(TZ_TAIPEI)
    print(f"===== 總經歷史行事曆：{HISTORY_START} ~ {today} =====")

    seen, items = set(), []
    for first, last in _months(HISTORY_START, today):
        try:
            html = M._fetch_raw_rows(first, last)
            evs = M._parse_events(html)
        except Exception as e:  # noqa: BLE001
            print(f"  {first:%Y-%m} 抓取失敗：{type(e).__name__}: {e}")
            continue
        kept = 0
        for e in evs:
            if not e.actual:                  # 只收已公布（有實際值）
                continue
            if e.dt_taipei > now:
                continue
            key = (e.dt_taipei.strftime("%Y-%m-%d"), e.country, e.name)
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "date": e.dt_taipei.strftime("%Y-%m-%d"),
                "time": e.dt_taipei.strftime("%H:%M"),
                "country": e.country,
                "name": e.name,
                "previous": e.previous or "",
                "forecast": e.forecast or "",
                "actual": e.actual or "",
                "impact": e.impact,
                "interpretation": M._interpret(e),
            })
            kept += 1
        print(f"  {first:%Y-%m}：原始 {len(evs)} 筆，收錄已公布 {kept} 筆")

    items.sort(key=lambda x: (x["date"], x["time"]))
    generated_at = now.strftime("%Y-%m-%d %H:%M")
    out = PUBLIC_DIR / "macro_history.json"
    out.write_text(json.dumps({"generated_at": generated_at,
                               "count": len(items), "items": items},
                              ensure_ascii=False), encoding="utf-8")
    print(f"已寫入 {out}（{out.stat().st_size:,} bytes）：{len(items)} 筆")
    # 逐月統計
    from collections import Counter
    c = Counter(it["date"][:7] for it in items)
    for mk in sorted(c):
        print(f"    {mk}: {c[mk]} 筆")


if __name__ == "__main__":
    main()
