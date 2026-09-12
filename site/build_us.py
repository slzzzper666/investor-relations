"""美股網站資料建置：白名單 → 近期財報 + AI 中英分析 → us_list.json / detail/us-*.json。

依賴 ir.us_earn（白名單/抓取/分析）與 ir.radar.us（行事曆）。
與 build_data.py 共用 detail/，各自只清自己的命名空間（台股 vs us-*）。

用法：
  python site/build_us.py                     沿用既有白名單
  python site/build_us.py --whitelist         用 Gemini 重生白名單
  python site/build_us.py --since 2026-06-15  回補：since 起每一季都收，不限最近 45 天
環境變數 IR_US_MAX 可覆寫每次分析上限（預設 25；回補時設大一點）。
"""
import json
import os
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
                        fetch_earnings_history, load_whitelist)
from ir.us_profile import (extract_business, fetch_profile,        # noqa: E402
                           fetch_quarters, peer_pe_by_sector)

log = get_logger("build_us")
TAIPEI = timezone(timedelta(hours=8))

MAX_ANALYSES = int(os.getenv("IR_US_MAX", "25"))  # 每次最多分析幾檔（依市值大→小取）
PROFILE_CACHE = BASE_DIR / ".us6q_cache.json"     # {sym: {asof, quarters, profile}}，隨 repo 提交
BUSINESS_DIR = ROOT_DIR / "data" / "us_business"  # {SYM}.json 族群／業務項目（Gemini，隨 repo 提交）


def _compose_summary(one_liner, highlights):
    return one_liner + "".join("\n• " + h for h in (highlights or []))


def _compose_view(ai_view, risks, label, sep):
    v = ai_view or ""
    if risks:
        v += "\n\n" + label + "：" + sep.join(risks)
    return v


def build_reported(whitelist, since: str | None = None):
    """since=None：每檔只看最近 45 天內公布的那一季（每日模式）。
    since 給日期：收 since 起每一季（回補模式；CI 停擺幾個月時用）。"""
    name_zh = {w["symbol"]: w.get("name_zh") or w["symbol"] for w in whitelist}
    syms = list(name_zh.keys())
    log.info("掃描 %d 檔白名單%s…", len(syms),
             f" {since} 起各季財報" if since else "近期財報")

    found = []
    for sym in syms:
        try:
            rows = (fetch_earnings_history(sym, since) if since
                    else [d for d in [fetch_earnings(sym)] if d])
        except Exception as e:  # noqa: BLE001
            log.warning("%s 抓取失敗：%s", sym, e)
            rows = []
        found.extend(rows)
        time.sleep(1.0)  # Yahoo 節流

    # 已有分析快取的季不算進上限，額度留給真正的新財報
    cache_dir = ROOT_DIR / "data" / "us_analysis"
    fresh = [d for d in found
             if not (cache_dir / f"{d['symbol']}_{d['report_date']}.json").exists()]
    cached = [d for d in found if d not in fresh]
    fresh.sort(key=lambda d: -(d.get("market_cap") or 0))
    targets = cached + fresh[:MAX_ANALYSES]
    log.info("已公布 %d 季（快取 %d、新 %d），本次分析新財報 %d 季",
             len(found), len(cached), len(fresh), min(len(fresh), MAX_ANALYSES))

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


def enrich_all(whitelist) -> None:
    """讓美股詳細頁與台股同版面：近 6 季序列、產業別、同業本益比、業務族群。

    對「全部」us-* detail 檔補欄位（不只本次新分析的），資料進 PROFILE_CACHE／
    BUSINESS_DIR 隨 repo 提交；同一檔只在出現更新的財報日時才重抓 yfinance，
    族群只在第一次見到該公司時才呼叫 Gemini。
    """
    name_zh = {w["symbol"]: w.get("name_zh") or w["symbol"] for w in whitelist}
    cache: dict = {}
    if PROFILE_CACHE.exists():
        try:
            cache = json.loads(PROFILE_CACHE.read_text(encoding="utf-8"))
        except ValueError:
            cache = {}
    BUSINESS_DIR.mkdir(parents=True, exist_ok=True)

    # 每檔最新財報日（由 detail 檔推得）→ 判斷快取是否過期
    latest: dict[str, str] = {}
    files = list(DETAIL_DIR.glob("us-*.json"))
    for f in files:
        sym, rd = f.stem.split("-", 2)[1:]
        if rd > latest.get(sym, ""):
            latest[sym] = rd

    refreshed = 0
    for sym, rd in sorted(latest.items()):
        ent = cache.get(sym) or {}
        if ent.get("asof") == rd and ent.get("quarters"):
            continue
        try:
            q = fetch_quarters(sym)
            prof = fetch_profile(sym)
        except Exception as e:  # noqa: BLE001
            log.warning("%s 輪廓抓取失敗：%s", sym, e)
            continue
        if q or prof:
            cache[sym] = {"asof": rd, "quarters": q or [], "profile": prof or {}}
            refreshed += 1
        time.sleep(1.0)
    log.info("美股輪廓：%d 檔、本次重抓 %d 檔", len(latest), refreshed)
    PROFILE_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    # 族群（Gemini）：只做還沒有檔的；額度耗盡就停，下次接著補
    new_biz = 0
    for sym in sorted(latest):
        bf = BUSINESS_DIR / f"{sym}.json"
        if bf.exists():
            continue
        prof = (cache.get(sym) or {}).get("profile") or {}
        try:
            biz = extract_business(sym, name_zh.get(sym, sym), prof)
        except Exception as e:  # noqa: BLE001
            log.warning("%s 族群抽取失敗：%s", sym, str(e)[:120])
            if "額度" in str(e):
                break
            continue
        bf.write_text(json.dumps({"symbol": sym, **biz}, ensure_ascii=False, indent=1),
                      encoding="utf-8")
        new_biz += 1
    if new_biz:
        log.info("美股族群：新建 %d 檔", new_biz)

    peer = peer_pe_by_sector({s: (c.get("profile") or {}) for s, c in cache.items()})

    # 回寫所有 detail
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        sym = d["code"]
        ent = cache.get(sym) or {}
        prof = ent.get("profile") or {}
        fin = d.get("financials") or {"market": "us"}
        fin["quarters"] = ent.get("quarters") or []
        fin["industry"] = prof.get("sector_zh") or ""
        fin["industry_en"] = prof.get("industry") or ""
        fin["industry_pe"] = peer.get(fin["industry"])
        if prof.get("pe") is not None and fin.get("pe") is None:
            fin["pe"] = prof["pe"]
        d["financials"] = fin
        bf = BUSINESS_DIR / f"{sym}.json"
        if bf.exists():
            try:
                b = json.loads(bf.read_text(encoding="utf-8"))
                if b.get("segments") or b.get("tags"):
                    d["business"] = {"segments": b.get("segments") or [],
                                     "tags": b.get("tags") or [], "as_of": "",
                                     "confidence": "medium", "source": "profile"}
            except ValueError:
                pass
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    log.info("美股詳細頁已補欄位：%d 檔（同業本益比 %d 個產業）", len(files), len(peer))


def main():
    force = "--whitelist" in sys.argv
    since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else None
    whitelist = load_whitelist(force=force)
    log.info("白名單 %d 檔", len(whitelist))

    DETAIL_DIR.mkdir(parents=True, exist_ok=True)
    # 不刪既有 us-* detail（保留歷史回補的場次）；本次抓到的新增/更新即可
    list_items, details = build_reported(whitelist, since=since)
    for d in details:
        (DETAIL_DIR / f"{d['id']}.json").write_text(
            json.dumps(d, ensure_ascii=False), encoding="utf-8")

    enrich_all(whitelist)

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
            # 首頁族群篩選用（與台股同欄位名）
            "industry": (dd.get("financials") or {}).get("industry") or "",
            "tags": (dd.get("business") or {}).get("tags") or [],
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
