"""拿掉「評論法說會的影片」轉成的假逐字稿，改用簡報 PDF 重新分析（一次性清理，可重跑）。

背景（2026-10）：ir/media 的 YouTube 搜尋曾把評論頻道（價值股雷達、恥股夯妮、Wa People…）
的影片當成法說會錄影，逐字稿與 AI 分析都是根據第三方評論寫的。media 已修；這支清既有的。

每場：Notion 清空「逐字稿」「分段」「YT」→ 下載 MOPS 簡報 → analyze(pdf) → 寫回摘要與觀點；
本機 data/transcripts、data/segments 的快取一併刪掉。
用法：python remove_commentary.py data/proofread/commentary_ids.txt [--dry-run]
"""
import argparse
import json
import re
from datetime import date

import config
from ir.analyze import analyze
from ir.logger import get_logger
from ir.mops import Conference, download_pdf
from ir.notion_db import _find_page, _get, _rich_chunks, analysis_texts

log = get_logger("ir.rm_commentary")
PUBLIC = config.BASE_DIR / "site" / "public"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids_file")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    ids = [l.strip() for l in open(args.ids_file, encoding="utf-8-sig") if l.strip()]
    n, ds_id = _get()
    ok = fail = 0
    for cid in ids:
        d = json.loads((PUBLIC / "detail" / f"{cid}.json").read_text(encoding="utf-8"))
        m = re.search(r"fileName=([^&]+\.pdf)", d.get("pdf_url") or "")
        y, mo, dd = map(int, d["date"].split("-"))
        conf = Conference(stock_code=d["code"], company_name=d["company"], market="",
                          date=date(y, mo, dd), time="", location="", summary="",
                          pdf_filename=m.group(1) if m else "")
        page = _find_page(n, ds_id, conf)
        if page is None:
            log.warning("%s：Notion 找不到頁面", cid)
            fail += 1
            continue
        props = {"逐字稿": {"rich_text": []}, "分段": {"rich_text": []}, "YT": {"url": None}}
        pdf = download_pdf(conf, config.DATA_DIR) if conf.pdf_filename else None
        if pdf:
            try:
                summary, view = analysis_texts(analyze(conf.company_name, conf.stock_code,
                                                       d["date"], pdf_path=pdf))
                props["重點摘要"] = {"rich_text": _rich_chunks(summary)}
                props["AI 觀點與未來方向分析"] = {"rich_text": _rich_chunks(view)}
            except Exception as e:  # noqa: BLE001
                log.warning("%s：簡報重新分析失敗，只清逐字稿（%s）", cid, str(e)[:120])
        else:
            log.warning("%s：沒有簡報，只清逐字稿（摘要仍是舊的）", cid)
        if args.dry_run:
            log.info("%s：將更新 %s", cid, list(props))
            continue
        n.pages.update(page_id=page["id"], properties=props)
        for f in (config.TRANSCRIPT_DIR / f"{cid}.txt", config.DATA_DIR / "segments" / f"{cid}.json"):
            if f.exists():
                f.unlink()
        ok += 1
        log.info("%s %s：已移除評論影片逐字稿%s", cid, conf.company_name,
                 "，摘要改由簡報重寫" if "重點摘要" in props else "")
    log.info("===== 完成 %d、失敗 %d =====", ok, fail)


if __name__ == "__main__":
    main()
