"""回補「MOPS 有公告、站上卻沒有」的法說會。

缺口來源：每日管線在 Railway 只跑「昨天」一次，失敗就永遠不會重試
（容器檔案系統暫時性、processed.json 不保留）。2026-09 查明的三個故障
（Gemini 2.0 下架、Groq llama 下架、irconference http→https 轉址讓 ffmpeg 卡死）
讓 7～9 月掉了上百場。

用法：
  python backfill_missing.py --since 2026-07-01              先跑 --dry-run 看清單
  python backfill_missing.py --since 2026-07-01 --dry-run
  python backfill_missing.py --since 2026-07-01 --audio      連影音逐字稿一起做（慢）

預設 --pdf-only（跳過影音與 STT，先求有資料）、不推播 TG/DC。
「站上有沒有」以 site/public/list.json 判定（＝Notion 的鏡像，需先跑 build_data）。
"""
import argparse
import json
from collections import defaultdict
from datetime import date

import config
from ir.logger import get_logger
from ir.mops import get_conferences_in_range
from main import _load_processed, _mark_processed, process_one

log = get_logger("ir.backfill")
SITE = config.BASE_DIR / "site" / "public"


def _missing(since: str, until: str) -> dict[str, list[dict]]:
    """{日期: [MOPS 公告但站上沒有的場次]}。"""
    up = json.loads((SITE / "upcoming.json").read_text(encoding="utf-8"))["items"]
    li = json.loads((SITE / "list.json").read_text(encoding="utf-8"))["items"]
    have = {(x["code"], x["date"]) for x in li}
    out: dict[str, list[dict]] = defaultdict(list)
    for u in up:
        if since <= u["date"] <= until and (u["code"], u["date"]) not in have:
            out[u["date"]].append(u)
    return dict(sorted(out.items()))


def main() -> None:
    ap = argparse.ArgumentParser(description="回補缺漏的法說會")
    ap.add_argument("--since", required=True, help="起始日 YYYY-MM-DD")
    ap.add_argument("--until", default=date.today().isoformat(), help="截止日（預設今天）")
    ap.add_argument("--dry-run", action="store_true", help="只列清單不處理")
    ap.add_argument("--audio", action="store_true", help="連影音與 STT 一起做（慢）")
    ap.add_argument("--push", action="store_true", help="推播 TG/DC（預設不推）")
    ap.add_argument("--limit", type=int, default=0, help="最多處理幾場")
    args = ap.parse_args()

    missing = _missing(args.since, args.until)
    total = sum(len(v) for v in missing.values())
    log.info("%s ~ %s 缺口：%d 天、%d 場", args.since, args.until, len(missing), total)

    # 整段區間只抓一次（每月 2 次請求）；逐日呼叫 get_conferences 會重複抓同一個月，
    # MOPS 對密集請求會逾時
    all_confs = {(c.stock_code, c.date.isoformat()): c
                 for c in get_conferences_in_range(date.fromisoformat(args.since),
                                                   date.fromisoformat(args.until))}
    processed = _load_processed()
    ok = fail = unseen = 0
    for day, rows in missing.items():
        # 這裡查不到的，代表每日管線當天也看不到（公告後改期等），要另外處理
        for code in sorted({r["code"] for r in rows}):
            conf = all_confs.get((code, day))
            name = next(r["name"] for r in rows if r["code"] == code)
            if conf is None:
                unseen += 1
                log.warning("%s %s %s：get_conferences 查不到（每日管線也看不到）",
                            day, code, name)
                continue
            key = f"{code}_{day}"
            if args.dry_run:
                log.info("%s %s %s：待補（簡報 %s、影音 %d 個）", day, code, name,
                         "有" if conf.pdf_filename else "無", len(conf.video_urls))
                continue
            if args.limit and ok + fail >= args.limit:
                log.info("已達上限 %d 場", args.limit)
                break
            try:
                process_one(conf, push=args.push, audio=args.audio)
                _mark_processed(key, processed)
                ok += 1
            except Exception:
                log.exception("%s %s 回補失敗", code, name)
                fail += 1

    log.info("===== 回補完成：成功 %d、失敗 %d、管線看不到 %d =====", ok, fail, unseen)


if __name__ == "__main__":
    main()
