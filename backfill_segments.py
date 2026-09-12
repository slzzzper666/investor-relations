"""逐字稿分段回補：站上有逐字稿、Notion 尚無「分段」的場次，用 Gemini 產錨點存回 Notion。

逐字稿從本機 site/public/detail/{id}.json 讀（build_data 剛從 Notion 拉下來的），
不再打 Notion 撈全文；只在寫回時查一次頁面。冪等，可中斷續跑。

用法：python backfill_segments.py [--limit N] [--dry-run]
"""
import argparse
import json
from datetime import date

import config
from ir.logger import get_logger
from ir.mops import Conference
from ir.notion_db import save_segments, status
from ir.segment import plan_segments

log = get_logger("ir.backfill_seg")
PUBLIC = config.BASE_DIR / "site" / "public"
SEG_DIR = config.DATA_DIR / "segments"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    items = json.loads((PUBLIC / "list.json").read_text(encoding="utf-8"))["items"]
    todo = [x for x in items if x.get("transcript_chars", 0) >= 800 and x.get("code")]
    todo.sort(key=lambda x: -(x.get("market_cap") or 0))
    log.info("站上有逐字稿的場次 %d 場（市值大→小）", len(todo))

    done = skipped = failed = 0
    for it in todo:
        if args.limit and done >= args.limit:
            break
        cid = it["id"]
        if (SEG_DIR / f"{cid}.json").exists():        # 人工版已有，尊重
            skipped += 1
            continue
        y, m, d = map(int, it["date"].split("-"))
        conf = Conference(stock_code=it["code"], company_name=it["company"], market="",
                          date=date(y, m, d), time="", location="", summary="")
        try:
            st = status(conf)
            if st is None or st.get("has_segments"):
                skipped += 1
                continue
            detail_f = PUBLIC / "detail" / f"{cid}.json"
            if not detail_f.exists():
                skipped += 1
                continue
            transcript = json.loads(detail_f.read_text(encoding="utf-8")).get("transcript", "")
            if args.dry_run:
                log.info("  待分段：%s %s（%d 字）", cid, it["company"], len(transcript))
                done += 1
                continue
            plan = plan_segments(it["company"], it["code"], it["date"], transcript)
            if plan and save_segments(conf, plan):
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
    log.info("===== 完成：分段 %d、跳過 %d、失敗 %d =====", done, skipped, failed)


if __name__ == "__main__":
    main()
