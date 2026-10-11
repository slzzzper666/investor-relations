"""既有逐字稿全面轉台灣繁體＋刪 Whisper 幻聽字樣（一次性遷移，可重跑）。

掃 Notion 全部有「逐字稿」的頁面：轉換後有變才寫回（逐字稿＋分段錨點一起轉）。
已是繁體的會被跳過，所以可以隨時重跑——例如回補程序還在跑時先跑一次，
回補結束後再跑一次把最後那批補上。本機快取（data/transcripts、data/segments）也一併轉。

用法：python convert_transcripts_tw.py [--dry-run] [--limit N]
"""
import argparse
import json
import time

from notion_client import Client

import config
from ir.logger import get_logger
from ir.notion_db import _rich_chunks
from ir.stt import strip_hallucinations
from ir.zh import segments_to_tw, to_tw

log = get_logger("ir.zh_migrate")


def _plain(prop: dict) -> str:
    return "".join(t.get("plain_text", "") for t in prop.get("rich_text") or [])


def convert_local() -> None:
    n = 0
    for f in config.TRANSCRIPT_DIR.glob("*.txt"):
        t = f.read_text(encoding="utf-8")
        c = to_tw(t)
        if c != t:
            f.write_text(c, encoding="utf-8")
            n += 1
    seg_dir = config.DATA_DIR / "segments"
    m = 0
    for f in seg_dir.glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        c = segments_to_tw(d)
        if c != d:
            f.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
            m += 1
    log.info("本機快取：逐字稿 %d 檔、分段錨點 %d 檔已轉換", n, m)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    n = Client(auth=config.NOTION_API_KEY)
    ds_id = n.databases.retrieve(database_id=config.NOTION_PARENT_ID)["data_sources"][0]["id"]

    cursor, seen, changed, same = None, 0, 0, 0
    while True:
        kw = {"data_source_id": ds_id, "page_size": 50,
              "filter": {"property": "逐字稿", "rich_text": {"is_not_empty": True}}}
        if cursor:
            kw["start_cursor"] = cursor
        for attempt in range(5):         # 查詢偶爾被 Notion 重置連線，重試即可
            try:
                res = n.data_sources.query(**kw)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("查詢失敗（第 %d 次）：%s", attempt + 1, str(e)[:100])
                time.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError("Notion 查詢連續失敗，稍後重跑即可（已轉的會跳過）")
        for page in res["results"]:
            seen += 1
            pr = page["properties"]
            tr = _plain(pr.get("逐字稿", {}))
            new_tr = strip_hallucinations(to_tw(tr))   # 順便刪 Whisper 幻聽字樣（明鏡與點點欄目…）
            seg_raw = _plain(pr.get("分段", {}))
            new_seg = seg_raw
            if seg_raw:
                try:
                    d = json.loads(seg_raw)
                    d = segments_to_tw(d)
                    for sg in (d or {}).get("segments") or []:
                        sg["start"] = strip_hallucinations(sg.get("start", ""))
                    new_seg = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
                except ValueError:
                    pass
            if new_tr == tr and (not seg_raw or json.loads(new_seg) == json.loads(seg_raw)):
                same += 1
                continue
            name = "".join(t.get("plain_text", "") for t in pr.get("公司", {}).get("title", []))
            date = ((pr.get("日期", {}).get("date") or {}).get("start") or "")[:10]
            if args.dry_run:
                log.info("  待轉：%s %s（%d 字）", date, name, len(tr))
            else:
                props = {"逐字稿": {"rich_text": _rich_chunks(new_tr)}}
                if seg_raw and new_seg != seg_raw:
                    props["分段"] = {"rich_text": _rich_chunks(new_seg)}
                for attempt in range(3):
                    try:
                        n.pages.update(page_id=page["id"], properties=props)
                        break
                    except Exception as e:  # noqa: BLE001
                        log.warning("%s %s 寫回失敗（第 %d 次）：%s", date, name, attempt + 1, str(e)[:100])
                        time.sleep(3)
                time.sleep(0.35)          # Notion 約每秒 3 次
            changed += 1
            if changed % 50 == 0:
                log.info("進度：已轉 %d、原本就繁體 %d（掃過 %d）", changed, same, seen)
            if args.limit and changed >= args.limit:
                break
        if (args.limit and changed >= args.limit) or not res.get("has_more"):
            break
        cursor = res["next_cursor"]

    log.info("===== Notion：掃 %d 篇，轉換 %d、原本就繁體 %d =====", seen, changed, same)
    if not args.dry_run:
        convert_local()


if __name__ == "__main__":
    main()
