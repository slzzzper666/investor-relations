"""人工審過的「改到數字」修正（data/proofread/review.jsonl 的 held）挑選後直接套用到 Notion。

proofread 的數字安全閥會把所有改到數字的修正擋下，其中很多其實是明確的代號錯字
（Silicon 420→Silicon Photonics、空8→空巴、48度→48V）。這支只套用指定序號，
分段錨點用同一批替換重播。

用法：python apply_held_fixes.py 0 1 2 5 ...   （序號＝review.jsonl 的行號，0 起算）
"""
import json
import sys
from datetime import date

import config
from ir.logger import get_logger
from ir.mops import Conference
from ir.notion_db import _find_page, _get, _rich_chunks, segments_to_text
from ir.proofread import replay_on_segments

log = get_logger("ir.held_fixes")
REVIEW = config.DATA_DIR / "proofread" / "review.jsonl"


def _plain(prop: dict) -> str:
    return "".join(t.get("plain_text", "") for t in prop.get("rich_text") or [])


def main() -> None:
    pick = {int(x) for x in sys.argv[1:]}
    rows = [json.loads(l) for l in REVIEW.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_id: dict[str, list] = {}
    for i, r in enumerate(rows):
        if i in pick and r["type"] == "held":
            by_id.setdefault(r["id"], []).append(r)
    n, ds_id = _get()
    for cid, fixes in by_id.items():
        code, d = cid.split("_")
        y, m, dd = map(int, d.split("-"))
        conf = Conference(stock_code=code, company_name="", market="", date=date(y, m, dd),
                          time="", location="", summary="")
        page = _find_page(n, ds_id, conf)
        if page is None:
            log.warning("%s：Notion 找不到", cid)
            continue
        text = _plain(page["properties"].get("逐字稿", {}))
        applied = []
        for f in fixes:
            kind = f.get("kind", "local")
            if f["wrong"] not in text:
                log.info("%s：原文已無「%s」，略過", cid, f["wrong"])
                continue
            text = text.replace(f["wrong"], f["right"]) if kind == "global" \
                else text.replace(f["wrong"], f["right"], 1)
            applied.append({"kind": kind, "wrong": f["wrong"], "right": f["right"]})
        if not applied:
            continue
        props = {"逐字稿": {"rich_text": _rich_chunks(text)}}
        seg_raw = _plain(page["properties"].get("分段", {}))
        if seg_raw:
            plan, _lost = replay_on_segments(json.loads(seg_raw), applied, text)
            if plan:
                props["分段"] = {"rich_text": _rich_chunks(segments_to_text(plan))}
        n.pages.update(page_id=page["id"], properties=props)
        log.info("%s：套用 %d 筆", cid, len(applied))


if __name__ == "__main__":
    main()
