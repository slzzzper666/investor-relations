"""「與上次法說會比較」回補：每家公司只補**最新一場**（有上一場可比的），存回 Notion「比較」欄位。

輸入用本機 site/public/detail/{id}.json（build_data 剛從 Notion 拉下來的摘要／AI 觀點／逐字稿），
不再打 Notion 撈全文；只在寫回前查一次頁面狀態（已有比較就跳過）。冪等，可中斷續跑。
之後的新場次由每日管線（main.py）在分析時順手產出，不必再回補。

用法：python backfill_compare.py [--limit N] [--dry-run] [--since YYYY-MM-DD]
"""
import argparse
import json
from datetime import date, timedelta

import config
from ir.compare import compare
from ir.logger import get_logger
from ir.mops import Conference
from ir.notion_db import MIN_GAP_DAYS, save_compare, status

log = get_logger("ir.backfill_cmp")
PUBLIC = config.BASE_DIR / "site" / "public"
COMPARE_DIR = config.DATA_DIR / "compare"


def _conf(it: dict) -> Conference:
    y, m, d = map(int, it["date"].split("-"))
    return Conference(stock_code=it["code"], company_name=it["company"], market="",
                      date=date(y, m, d), time="", location="", summary="")


def _load_detail(cid: str) -> dict | None:
    f = PUBLIC / "detail" / f"{cid}.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    return {"id": cid, "date": d["date"], "summary": d.get("summary") or "",
            "ai_view": d.get("ai_view") or "", "transcript": d.get("transcript") or ""}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", default="", help="只補最新一場在此日期之後的公司")
    args = ap.parse_args()

    items = json.loads((PUBLIC / "list.json").read_text(encoding="utf-8"))["items"]
    by_code: dict[str, list[dict]] = {}
    for it in items:
        if it.get("code"):
            by_code.setdefault(it["code"], []).append(it)
    todo = []
    for code, confs in by_code.items():
        confs.sort(key=lambda x: x["date"], reverse=True)
        if len(confs) < 2 or (args.since and confs[0]["date"] < args.since):
            continue
        # 與管線同規則：上一場須早 MIN_GAP_DAYS 天以上（隔天的中英文場不算上一場）
        y, m, d = map(int, confs[0]["date"].split("-"))
        cutoff = (date(y, m, d) - timedelta(days=MIN_GAP_DAYS)).isoformat()
        prev = next((c for c in confs[1:] if c["date"] <= cutoff), None)
        if prev:
            todo.append((confs[0], prev))
    todo.sort(key=lambda t: t[0]["date"], reverse=True)     # 近期優先
    log.info("有上一場可比的公司 %d 家（最新一場日期新→舊）", len(todo))

    done = skipped = failed = 0
    for cur_it, prev_it in todo:
        if args.limit and done >= args.limit:
            break
        cid = cur_it["id"]
        if (COMPARE_DIR / f"{cid}.json").exists():        # 本機示範版已有，尊重
            skipped += 1
            continue
        conf = _conf(cur_it)
        try:
            st = status(conf)
            if st is None or st.get("has_compare"):
                skipped += 1
                continue
            cur, prev = _load_detail(cid), _load_detail(prev_it["id"])
            if not cur or not prev or not (cur["summary"] and prev["summary"]):
                skipped += 1
                continue
            if args.dry_run:
                log.info("  待比較：%s %s vs %s", cid, cur_it["company"], prev["date"])
                done += 1
                continue
            result = compare(cur_it["company"], cur_it["code"], cur, prev)
            if result and save_compare(conf, result):
                done += 1
            else:
                failed += 1
        except KeyboardInterrupt:
            break
        except Exception as e:  # noqa: BLE001
            failed += 1
            log.warning("%s：失敗 %s", cid, str(e)[:140])
            if "額度" in str(e):
                log.warning("Gemini 額度耗盡，中止（可續跑）")
                break
    log.info("===== 完成：比較 %d、跳過 %d、失敗 %d =====", done, skipped, failed)


if __name__ == "__main__":
    main()
