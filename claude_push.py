"""把 Claude 產出的分析（results.json）推上 Notion 並標記完成。
results.json 格式：[{id, one_liner, highlights[], outlook, ai_view}, ...]
"""
import json
from datetime import date

import config
from ir.mops import Conference
from ir.notion_db import upsert_conference
from main import _load_processed, _mark_processed

QUEUE = config.DATA_DIR / "claude_queue"
batch = json.loads((QUEUE / "batch.json").read_text(encoding="utf-8"))
results = json.loads((QUEUE / "results.json").read_text(encoding="utf-8"))
rmap = {r["id"]: r for r in results}
processed = _load_processed()

done = 0
for b in batch:
    r = rmap.get(b["id"])
    if not r:
        continue
    y, mo, dd = map(int, b["date"].split("-"))
    conf = Conference(stock_code=b["code"], company_name=b["company"], market="",
                      date=date(y, mo, dd), time="", location="", summary="",
                      pdf_filename=b.get("pdf", ""))
    analysis = {"one_liner": r["one_liner"], "highlights": r["highlights"],
                "outlook": r["outlook"], "ai_view": r["ai_view"]}
    transcript = b["content"] if b["source"] == "逐字稿" else ""
    upsert_conference(conf, analysis, transcript, "")
    _mark_processed(b["id"], processed)
    done += 1
print(f"已推送 {done} 場到 Notion")
