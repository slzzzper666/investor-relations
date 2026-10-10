"""既有逐字稿批次文字校對（不重聽音檔）：每篇 1 次 Gemini 文字呼叫，修正清單由程式套用。

- 來源：本機 site/public/detail/{id}.json 的全文（build_data 剛從 Notion 拉下來的）；
  分段錨點從 Notion「分段」欄位讀，套用同一批替換後驗證，對不上太多就重新分段。
- 改到數字的修正不套用，彙整到 data/proofread/review.md 給人確認；
  會讓既有公司名稱減少的修正直接拒絕（見 ir/proofread）。
- 「中文場卻有多段英文長句」的場次（Whisper 自行翻譯造成內容消失）文字救不回來，
  這裡跳過，改用 backfill_transcripts.py --ids 重聽音檔。
- 冪等、可中斷：做完的記在 data/proofread/done.json；Gemini 額度用盡就停，隔天續跑。

用法：python proofread_backfill.py [--limit N] [--dry-run] [--id 6257_2026-10-06]
"""
import argparse
import json
import re
import time
from datetime import date

import config
from ir.gemini_util import exhausted_for
from ir.logger import get_logger
from ir.mops import Conference
from ir.notion_db import _find_page, _get, _rich_chunks, segments_to_text
from ir.proofread import PROOF_MODELS, _ctx, candidates_for, proofread, replay_on_segments
from ir.segment import plan_segments
from ir.zh import segments_to_tw, to_tw

log = get_logger("ir.proofread_bf")
PUBLIC = config.BASE_DIR / "site" / "public"
STATE_DIR = config.DATA_DIR / "proofread"
DONE = STATE_DIR / "done.json"
REVIEW = STATE_DIR / "review.jsonl"
CLAUDE_IN = STATE_DIR / "claude_in"        # --export：給 Claude 逐篇校對的輸入（背景資料＋全文）
CLAUDE_FIX = STATE_DIR / "claude_fixes"    # Claude 寫回的修正清單 {cid}.json，--claude 套用
ENGLISH_RUN = re.compile(r"(?:[A-Za-z']+[\s,.]+){8,}")


def suspect_english(t: str) -> bool:
    """中文為主卻有 ≥3 段英文長句：Whisper 誤判語言、自行翻譯的特徵。"""
    return len(re.findall(r"[一-鿿]", t)) > 2000 and len(ENGLISH_RUN.findall(t)) >= 3


def _plain(prop: dict) -> str:
    return "".join(t.get("plain_text", "") for t in prop.get("rich_text") or [])


def _load_done() -> set[str]:
    return set(json.loads(DONE.read_text(encoding="utf-8"))) if DONE.exists() else set()


def _save_done(done: set[str]) -> None:
    DONE.write_text(json.dumps(sorted(done), ensure_ascii=False, indent=0), encoding="utf-8")


def write_review_md() -> None:
    """把所有待確認（改到數字）與拒絕的修正整理成一份 Markdown。"""
    if not REVIEW.exists():
        return
    rows = [json.loads(line) for line in REVIEW.read_text(encoding="utf-8").splitlines() if line.strip()]
    held = [r for r in rows if r["type"] == "held"]
    rej = [r for r in rows if r["type"] == "rejected"]
    out = ["# 逐字稿校對：待確認清單", "",
           "AI 提出、但程式沒有自動套用的修正。確認後可手動在 Notion 修改，或告訴 Claude 要套用哪些。", "",
           f"## 改到數字的修正（{len(held)} 筆，未套用）", "",
           "| 場次 | 原文 | AI 建議 |", "|---|---|---|"]
    out += [f"| {r['id']} | {r['wrong']} | {r['right']} |" for r in held]
    out += ["", f"## 會讓既有公司名稱減少而被拒絕（{len(rej)} 筆）", "",
            "| 場次 | 原文 | AI 建議 |", "|---|---|---|"]
    out += [f"| {r['id']} | {r['wrong']} | {r['right']} |" for r in rej]
    (STATE_DIR / "review.md").write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--id", default="", help="只做這一場（測試用）")
    ap.add_argument("--export", type=int, default=0,
                    help="匯出下 N 篇待校對稿到 data/proofread/claude_in/，給 Claude 人工校對")
    ap.add_argument("--by-cap", action="store_true", help="--export 依市值大→小挑")
    ap.add_argument("--claude", action="store_true",
                    help="不呼叫 Gemini，只套用 data/proofread/claude_fixes/ 裡 Claude 寫好的修正清單")
    args = ap.parse_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    items = json.loads((PUBLIC / "list.json").read_text(encoding="utf-8"))["items"]
    todo = [x for x in items if x.get("transcript_chars", 0) >= 300 and x.get("code")
            and (not args.id or x["id"] == args.id)]
    todo.sort(key=lambda x: x["date"], reverse=True)          # 新場次優先（最常被看）
    same_day: dict[str, list[str]] = {}       # 聯合法說會：同一天的公司一起做同音比對
    for x in items:
        same_day.setdefault(x["date"], []).append(x["company"])
    done = _load_done()
    log.info("有逐字稿 %d 篇，已校對 %d 篇", len(todo), len(done & {x["id"] for x in todo}))

    if args.export:
        if args.by_cap:                       # 先做市值大的（最多人看）
            todo.sort(key=lambda x: -(x.get("market_cap") or 0))
        CLAUDE_IN.mkdir(parents=True, exist_ok=True)
        k = 0
        for it in todo:
            cid = it["id"]
            if k >= args.export:
                break
            f = PUBLIC / "detail" / f"{cid}.json"
            if cid in done or (CLAUDE_FIX / f"{cid}.json").exists() or not f.exists():
                continue
            text = json.loads(f.read_text(encoding="utf-8")).get("transcript") or ""
            if suspect_english(text):
                continue
            cands = candidates_for(text, it["company"], same_day.get(it["date"], []))
            homo = "、".join(f"{w}（{c} 次，音同{nm}）" for w, (c, nm) in list(cands.items())[:12])
            peers = "、".join(same_day.get(it["date"], []))[:300]
            # 每 300 字斷一行：逐字稿常是一整行，太長的行讀檔工具會截斷
            body = "\n".join(text[i:i + 300] for i in range(0, len(text), 300))
            (CLAUDE_IN / f"{cid}.txt").write_text(
                f"{it['date']}\n{_ctx(it['company'], it['code'])}\n同日法說會：{peers}\n"
                f"同音候選：{homo or '—'}\n=====\n{body}", encoding="utf-8")
            k += 1
        log.info("已匯出 %d 篇到 %s", k, CLAUDE_IN)
        return

    n, ds_id = _get()
    ok = skipped = english = failed = 0
    for it in todo:
        if args.limit and ok >= args.limit:
            break
        cid = it["id"]
        if cid in done and not args.id:
            continue
        f = PUBLIC / "detail" / f"{cid}.json"
        if not f.exists():
            skipped += 1
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        text = d.get("transcript") or ""
        if suspect_english(text):
            english += 1
            continue
        fx = None
        if args.claude:
            ff = CLAUDE_FIX / f"{cid}.json"
            if not ff.exists():
                continue
            fx = json.loads(ff.read_text(encoding="utf-8"))
        if args.dry_run:
            ok += 1
            continue
        if fx is None and exhausted_for(PROOF_MODELS):
            log.warning("Gemini 主力模型今日額度已用完，停在這裡（明天續跑）")
            break
        y, m, dd = map(int, it["date"].split("-"))
        conf = Conference(stock_code=it["code"], company_name=it["company"], market="",
                          date=date(y, m, dd), time="", location="", summary="")
        try:
            new, rep = proofread(text, it["company"], it["code"], it["date"],
                                 also=same_day.get(it["date"], []),
                                 fixes=fx, model="claude" if fx is not None else "")
            new = to_tw(new)
            with REVIEW.open("a", encoding="utf-8") as fh:
                for kind in ("held", "rejected"):
                    for h in rep.get(kind, []):
                        fh.write(json.dumps({"type": kind, "id": cid, **h}, ensure_ascii=False) + "\n")
            if new == text:
                done.add(cid); _save_done(done); ok += 1
                continue
            page = _find_page(n, ds_id, conf)
            if page is None:
                skipped += 1
                continue
            props = {"逐字稿": {"rich_text": _rich_chunks(new)}}
            seg_raw = _plain(page["properties"].get("分段", {}))
            if seg_raw:
                old_plan = segments_to_tw(json.loads(seg_raw))   # 錨點與新稿同一套字形與標點
                plan, lost = replay_on_segments(old_plan, rep["applied"], new)
                total = len(old_plan.get("segments") or [])
                if plan is None or (total and lost / total > 0.3):   # 錨點掉太多 → 重新分段
                    plan = plan_segments(it["company"], it["code"], it["date"], new)
                    log.info("%s：分段錨點對不上（掉 %d），已重新分段", cid, lost)
                if plan:
                    props["分段"] = {"rich_text": _rich_chunks(segments_to_text(plan))}
            n.pages.update(page_id=page["id"], properties=props)
            t_file = config.TRANSCRIPT_DIR / f"{cid}.txt"
            if t_file.exists():
                t_file.write_text(new, encoding="utf-8")
            done.add(cid); _save_done(done); ok += 1
            time.sleep(0.4)
        except KeyboardInterrupt:
            break
        except Exception as e:  # noqa: BLE001
            failed += 1
            log.warning("%s：失敗 %s", cid, str(e)[:160])
            if "額度" in str(e):
                break
    write_review_md()
    log.info("===== 完成：校對 %d、跳過 %d、疑似亂翻英文留待重聽 %d、失敗 %d =====",
             ok, skipped, english, failed)


if __name__ == "__main__":
    main()
