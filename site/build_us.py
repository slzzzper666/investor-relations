"""美股網站資料建置：白名單 → 近期財報 + AI 中英分析 → us_list.json / detail/us-*.json。

依賴 ir.us_earn（白名單/抓取/分析）與 ir.radar.us（行事曆）。
與 build_data.py 共用 detail/，各自只清自己的命名空間（台股 vs us-*）。

用法：
  .venv\\Scripts\\python site\\build_us.py             沿用既有白名單
  .venv\\Scripts\\python site\\build_us.py --whitelist  用 Gemini 重生白名單
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
PUBLIC_DIR = BASE_DIR / "public"
DETAIL_DIR = PUBLIC_DIR / "detail"

sys.path.insert(0, str(ROOT_DIR))
from ir.logger import get_logger                                    # noqa: E402
from ir.radar import us as radar_us                                 # noqa: E402
from ir.us_earn import (analyze_cached, fetch_earnings,             # noqa: E402
                        load_whitelist)

log = get_logger("build_us")
TAIPEI = timezone(timedelta(hours=8))

MAX_ANALYSES = 25  # 每次最多分析幾檔（依市值大→小取）


def _compose_summary(one_liner, highlights):
    return one_liner + "".join("\n• " + h for h in (highlights or []))


def _compose_view(ai_view, risks, label, sep):
    v = ai_view or ""
    if risks:
        v += "\n\n" + label + "：" + sep.join(risks)
    return v


def build_reported(whitelist):
    name_zh = {w["symbol"]: w.get("name_zh") or w["symbol"] for w in whitelist}
    syms = list(name_zh.keys())
    log.info("掃描 %d 檔白名單近期財報…", len(syms))

    found = []
    for sym in syms:
        try:
            data = fetch_earnings(sym)
        except Exception as e:  # noqa: BLE001
            log.warning("%s 抓取失敗：%s", sym, e)
            data = None
        if data:
            found.append(data)
        time.sleep(1.0)  # Yahoo 節流

    found.sort(key=lambda d: -(d.get("market_cap") or 0))
    targets = found[:MAX_ANALYSES]
    log.info("近期已公布 %d 檔，分析前 %d 檔", len(found), len(targets))

    list_items, details = [], []
    for data in targets:
        sym, rd = data["symbol"], data["report_date"]
        try:
            a = analyze_cached(data)
        except Exception as e:  # noqa: BLE001
            log.warning("%s 分析失敗：%s", sym, e)
            continue
        it_id = f"us-{sym}-{rd}"
        summary_zh = _compose_summary(a["one_liner_zh"], a.get("highlights_zh"))
        summary_en = _compose_summary(a["one_liner_en"], a.get("highlights_en"))
        view_zh = _compose_view(a["ai_view_zh"], a.get("risks_zh"), "需留意", "；")
        view_en = _compose_view(a["ai_view_en"], a.get("risks_en"), "Watch", "; ")
        fin = {
            "market": "us", "report_date": rd,
            "eps": data.get("eps_actual"), "eps_estimate": data.get("eps_estimate"),
            "surprise_pct": data.get("surprise_pct"),
            "revenue": data.get("revenue"), "revenue_yoy": data.get("revenue_yoy"),
            "pe": data.get("pe"), "market_cap": data.get("market_cap"),
            "price_reaction": data.get("price_reaction"),
        }
        details.append({
            "id": it_id, "market": "us",
            "company": name_zh.get(sym, sym),
            "company_en": data.get("name_en") or sym,
            "code": sym, "date": rd,
            "pdf_url": "", "video_url": "", "transcript": "",
            "summary": summary_zh, "ai_view": view_zh,
            "summary_zh": summary_zh, "summary_en": summary_en,
            "ai_view_zh": view_zh, "ai_view_en": view_en,
            "beat_or_miss": a.get("beat_or_miss", ""),
            "financials": fin,
        })
        list_items.append({
            "id": it_id, "code": sym, "company": name_zh.get(sym, sym),
            "company_en": data.get("name_en") or sym, "date": rd,
            "market_cap": 0,  # 美股市值單位與台股不同，列表不顯示市值
            "pdf_url": "", "video_url": "",
            "summary": summary_zh, "ai_view": view_zh,
            "has_transcript": False, "transcript_chars": 0,
        })

    list_items.sort(key=lambda x: (x["date"], x["code"]), reverse=True)
    details.sort(key=lambda x: (x["date"], x["code"]), reverse=True)
    return list_items, details


def build_upcoming(whitelist):
    wl = {w["symbol"] for w in whitelist}
    today = datetime.now(radar_us.TZ_US_EAST).date()
    end = today + timedelta(days=30)
    try:
        evs = radar_us.fetch_calendar(today, end)
    except Exception as e:  # noqa: BLE001
        log.warning("美股行事曆抓取失敗：%s", e)
        return []
    items, seen = [], set()
    for e in evs:
        if e.symbol not in wl or (e.symbol, e.date) in seen:
            continue
        seen.add((e.symbol, e.date))
        items.append({"code": e.symbol, "name": e.name, "date": e.date,
                      "time": e.session or "", "market_cap": 0})
    items.sort(key=lambda x: (x["date"], x["code"]))
    return items


def main():
    force = "--whitelist" in sys.argv
    whitelist = load_whitelist(force=force)
    log.info("白名單 %d 檔", len(whitelist))

    DETAIL_DIR.mkdir(parents=True, exist_ok=True)
    # 不刪既有 us-* detail（保留歷史回補的場次）；本次抓到的新增/更新即可
    list_items, details = build_reported(whitelist)
    for d in details:
        (DETAIL_DIR / f"{d['id']}.json").write_text(
            json.dumps(d, ensure_ascii=False), encoding="utf-8")

    upcoming = build_upcoming(whitelist)
    up_path = PUBLIC_DIR / "us_upcoming.json"
    # 防呆：Nasdaq 行事曆雲端可能被擋而抓到 0 → 別用空清單覆蓋既有版本
    write_upcoming = True
    if not upcoming and up_path.exists():
        try:
            if json.loads(up_path.read_text(encoding="utf-8")).get("count", 0) > 0:
                log.warning("美股行事曆抓到 0 筆（疑似 Nasdaq 擋雲端），保留既有版本不覆蓋")
                write_upcoming = False
        except (ValueError, OSError):
            pass

    # us_list.json 由「全部」us-* detail 檔重建（含歷史回補），日期新→舊
    all_items = []
    for f in DETAIL_DIR.glob("us-*.json"):
        dd = json.loads(f.read_text(encoding="utf-8"))
        all_items.append({
            "id": dd["id"], "code": dd["code"], "company": dd["company"],
            "company_en": dd.get("company_en") or dd["code"], "date": dd["date"],
            "market_cap": 0, "pdf_url": "", "video_url": "",
            "summary": dd["summary"], "ai_view": dd["ai_view"],
            "has_transcript": False, "transcript_chars": 0,
        })
    all_items.sort(key=lambda x: (x["date"], x["code"]), reverse=True)

    generated_at = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
    (PUBLIC_DIR / "us_list.json").write_text(
        json.dumps({"generated_at": generated_at, "count": len(all_items),
                    "items": all_items}, ensure_ascii=False), encoding="utf-8")
    if write_upcoming:
        up_path.write_text(
            json.dumps({"generated_at": generated_at, "count": len(upcoming),
                        "items": upcoming}, ensure_ascii=False), encoding="utf-8")

    print(f"美股：分析 {len(list_items)} 檔、行事曆 {len(upcoming)} 筆")
    for li in list_items:
        print(f"  {li['date']}  {li['code']:>5}  {li['company']}")


if __name__ == "__main__":
    main()
