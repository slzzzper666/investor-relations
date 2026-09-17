"""「與上一季法說會比較」回補：每家公司只補**最新一場**，存回 Notion「比較」欄位。

比較對象是「上一季的最後一場」——同一季公司常開好幾場（自辦＋券商協辦），
同季互比只會逐條重複（規則見 ir/season）。

輸入用本機 site/public/detail/{id}.json（build_data 剛從 Notion 拉下來的摘要／AI 觀點／逐字稿），
不再打 Notion 撈全文；只在寫回前查一次頁面狀態（已有比較就跳過）。冪等，可中斷續跑。
之後的新場次由每日管線（main.py）在分析時順手產出，不必再回補。

用法：python backfill_compare.py [--limit N] [--dry-run] [--since YYYY-MM-DD] [--refresh]
  --refresh：把舊規則留下的「同季比較」重做一次（其餘已有比較的照樣跳過）
"""
import argparse
import json
from datetime import date

import config
from ir.compare import compare
from ir.logger import get_logger
from ir.mops import Conference
from ir.notion_db import save_compare, status
from ir.season import pick_previous, season_of

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
    return {"id": cid, "date": d["date"], "season": season_of(d["date"]),
            "summary": d.get("summary") or "", "ai_view": d.get("ai_view") or "",
            "transcript": d.get("transcript") or "", "compare": d.get("compare")}


def _stale(cur: dict) -> bool:
    """舊規則（只看間隔 21 天）留下的同季比較 → 值得重做。"""
    c = cur.get("compare") or {}
    return bool(c.get("prev_date")) and season_of(c["prev_date"]) == cur["season"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", default="", help="只補最新一場在此日期之後的公司")
    ap.add_argument("--refresh", action="store_true", help="重做同季比較（舊規則留下的）")
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
        # 與管線同規則：上一季的最後一場（見 ir/season）
        prev = pick_previous(confs[0]["date"], confs[1:])
        if prev:
            todo.append((confs[0], prev))
    todo.sort(key=lambda t: t[0]["date"], reverse=True)     # 近期優先
    log.info("有上一季可比的公司 %d 家（最新一場日期新→舊）%s",
             len(todo), "· 含重做同季比較" if args.refresh else "")

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
            cur, prev = _load_detail(cid), _load_detail(prev_it["id"])
            if not cur or not prev or not (cur["summary"] and prev["summary"]):
                skipped += 1
                continue
            redo = args.refresh and _stale(cur)
            st = status(conf)
            if st is None or (st.get("has_compare") and not redo):
                skipped += 1
                continue
            if args.dry_run:
                log.info("  待比較：%s %s（%s）vs %s（%s）%s", cid, cur_it["company"],
                         cur["season"], prev["date"], prev["season"],
                         "· 重做同季" if redo else "")
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
