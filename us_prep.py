"""為 Claude 美股分析備料：白名單中近期已公布、尚未產出 detail 的場次寫成一批。

財報數據用 yfinance（不耗 AI 額度）；分析交給 Claude（us_push.py 回寫）。
用法：python us_prep.py [N]
"""
import json
import sys

import config
from ir.us_earn import fetch_earnings, load_whitelist

N = int(sys.argv[1]) if len(sys.argv) > 1 else 8
QUEUE = config.DATA_DIR / "claude_queue"
QUEUE.mkdir(exist_ok=True)
DETAIL_DIR = config.BASE_DIR / "site" / "public" / "detail"


def _done_ids() -> set[str]:
    return {f.stem for f in DETAIL_DIR.glob("us-*.json")}  # us-AAPL-2026-04-30


def main() -> None:
    done = _done_ids()
    wl = load_whitelist()
    batch = []
    for w in wl:
        sym = w["symbol"]
        try:
            data = fetch_earnings(sym)
        except Exception as e:  # noqa: BLE001
            print(f"略過 {sym}：{e}")
            continue
        if not data:
            continue
        iid = f"us-{sym}-{data['report_date']}"
        if iid in done:
            continue
        batch.append({
            "id": iid, "symbol": sym,
            "name_zh": w.get("name_zh") or sym,
            "name_en": data.get("name_en") or sym,
            "sector": data.get("sector", ""),
            "report_date": data["report_date"],
            "eps_actual": data.get("eps_actual"),
            "eps_estimate": data.get("eps_estimate"),
            "surprise_pct": data.get("surprise_pct"),
            "revenue": data.get("revenue"),
            "revenue_yoy": data.get("revenue_yoy"),
            "pe": data.get("pe"),
            "market_cap": data.get("market_cap"),
            "price_reaction": data.get("price_reaction"),
        })
        if len(batch) >= N:
            break

    (QUEUE / "us_batch.json").write_text(
        json.dumps(batch, ensure_ascii=False), encoding="utf-8")
    print(f"已備料 {len(batch)} 檔美股：")
    for b in batch:
        print(f"  {b['symbol']} {b['name_zh']} {b['report_date']} "
              f"EPS {b['eps_actual']} 營收 {b['revenue']}")


if __name__ == "__main__":
    main()
