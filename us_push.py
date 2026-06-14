"""把 Claude 美股分析（us_results.json）寫成 detail/us-*.json 並重建 us_list.json。

us_results.json 格式：[{id, one_liner_zh, one_liner_en, beat_or_miss,
  highlights_zh[], highlights_en[], risks_zh[], risks_en[], ai_view_zh, ai_view_en}]
"""
import json
from datetime import datetime, timedelta, timezone

import config

QUEUE = config.DATA_DIR / "claude_queue"
DETAIL_DIR = config.BASE_DIR / "site" / "public" / "detail"
PUBLIC = config.BASE_DIR / "site" / "public"
TAIPEI = timezone(timedelta(hours=8))


def _summary(one, hl):
    return one + "".join("\n• " + h for h in (hl or []))


def _view(v, risks, label, sep):
    v = v or ""
    if risks:
        v += "\n\n" + label + "：" + sep.join(risks)
    return v


def main() -> None:
    batch = json.loads((QUEUE / "us_batch.json").read_text(encoding="utf-8"))
    results = json.loads((QUEUE / "us_results.json").read_text(encoding="utf-8"))
    rmap = {r["id"]: r for r in results}

    done = 0
    for b in batch:
        r = rmap.get(b["id"])
        if not r:
            continue
        sym, rd = b["symbol"], b["report_date"]
        summ_zh = _summary(r["one_liner_zh"], r.get("highlights_zh"))
        summ_en = _summary(r["one_liner_en"], r.get("highlights_en"))
        view_zh = _view(r["ai_view_zh"], r.get("risks_zh"), "需留意", "；")
        view_en = _view(r["ai_view_en"], r.get("risks_en"), "Watch", "; ")
        fin = {
            "market": "us", "report_date": rd,
            "eps": b.get("eps_actual"), "eps_estimate": b.get("eps_estimate"),
            "surprise_pct": b.get("surprise_pct"), "revenue": b.get("revenue"),
            "revenue_yoy": b.get("revenue_yoy"), "pe": b.get("pe"),
            "market_cap": b.get("market_cap"),
            "price_reaction": b.get("price_reaction"),
        }
        detail = {
            "id": b["id"], "market": "us",
            "company": b["name_zh"], "company_en": b["name_en"],
            "code": sym, "date": rd,
            "pdf_url": "", "video_url": "", "transcript": "",
            "summary": summ_zh, "ai_view": view_zh,
            "summary_zh": summ_zh, "summary_en": summ_en,
            "ai_view_zh": view_zh, "ai_view_en": view_en,
            "beat_or_miss": r.get("beat_or_miss", ""), "financials": fin,
        }
        (DETAIL_DIR / f"{b['id']}.json").write_text(
            json.dumps(detail, ensure_ascii=False), encoding="utf-8")
        done += 1

    # 重建 us_list.json：掃描所有 us-* detail
    items = []
    for f in sorted(DETAIL_DIR.glob("us-*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        items.append({
            "id": d["id"], "code": d["code"], "company": d["company"],
            "company_en": d.get("company_en", ""), "date": d["date"],
            "market_cap": 0, "pdf_url": "", "video_url": "",
            "summary": d["summary"], "ai_view": d["ai_view"],
            "has_transcript": False, "transcript_chars": 0,
        })
    items.sort(key=lambda x: (x["date"], x["code"]), reverse=True)
    gen = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
    (PUBLIC / "us_list.json").write_text(
        json.dumps({"generated_at": gen, "count": len(items), "items": items},
                   ensure_ascii=False), encoding="utf-8")
    print(f"已寫入 {done} 檔美股 detail，us_list.json 共 {len(items)} 檔")


if __name__ == "__main__":
    main()
