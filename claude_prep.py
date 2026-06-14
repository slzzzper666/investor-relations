"""為 Claude 分析備料：挑「市值最大、文字已就緒」的未處理場次，寫成一批。

文字來源優先：已備好的逐字稿 > 簡報 PDF 抽文字。
有影音但尚未轉錄的場次跳過（交給 GPU 備檔），純佔位無來源者也跳過。
用法：python claude_prep.py [N]
"""
import json
import sys
from datetime import date

import config
from ir.analyze import _pdf_text
from ir.mops import Conference, _fetch_month, download_pdf
from main import _load_processed

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5
QUEUE = config.DATA_DIR / "claude_queue"
QUEUE.mkdir(exist_ok=True)
WORKLIST = QUEUE / "worklist.json"
MAX_CHARS = 20000


def build_worklist() -> list[dict]:
    mcap_file = config.BASE_DIR / "site" / ".mcap_cache.json"
    mcap = {}
    if mcap_file.exists():
        mcap = json.loads(mcap_file.read_text(encoding="utf-8")).get("caps", {})
    today = date.today()
    seen: set[str] = set()
    items: list[dict] = []
    for y, m in [(2026, mm) for mm in (6, 5, 4, 3, 2, 1)]:
        for market in ("sii", "otc"):
            try:
                rows = _fetch_month(market, date(y, m, 15))
            except Exception:
                continue
            for c in rows:
                if c.date.year != y or c.date.month != m or c.date > today:
                    continue
                key = f"{c.stock_code}_{c.date.isoformat()}"
                if key in seen:
                    continue
                seen.add(key)
                items.append({"id": key, "code": c.stock_code,
                              "company": c.company_name, "date": c.date.isoformat(),
                              "pdf": c.pdf_filename, "mcap": mcap.get(c.stock_code, 0)})
    items.sort(key=lambda x: (x["date"][:7], x["mcap"]), reverse=True)
    WORKLIST.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return items


worklist = (json.loads(WORKLIST.read_text(encoding="utf-8"))
            if WORKLIST.exists() else build_worklist())
processed = _load_processed()

batch = []
for it in worklist:
    if it["id"] in processed:
        continue
    t_file = config.TRANSCRIPT_DIR / f"{it['id']}.txt"
    if t_file.exists():
        content, source = t_file.read_text(encoding="utf-8"), "逐字稿"
    elif it["pdf"]:
        y, mo, dd = map(int, it["date"].split("-"))
        conf = Conference(stock_code=it["code"], company_name=it["company"],
                          market="", date=date(y, mo, dd), time="", location="",
                          summary="", pdf_filename=it["pdf"])
        pdf = download_pdf(conf, config.DATA_DIR)
        if not pdf:
            continue
        content = _pdf_text(pdf)
        if len(content) < 200:  # 掃描型/空 PDF，無文字可分析
            continue
        source = "簡報PDF"
    else:
        continue
    batch.append({**it, "source": source, "content": content[:MAX_CHARS]})
    if len(batch) >= N:
        break

(QUEUE / "batch.json").write_text(json.dumps(batch, ensure_ascii=False),
                                  encoding="utf-8")
print(f"已備料 {len(batch)} 場：")
for b in batch:
    print(f"  {b['code']} {b['company']} {b['date']} [{b['source']}] "
          f"{len(b['content'])}字 市值{b['mcap']}億")
