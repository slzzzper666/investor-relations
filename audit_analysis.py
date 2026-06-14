"""分析內容體檢：查 Notion 各筆，揪出佔位/空白/異常的分析。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from notion_client import Client


def _rich(prop):
    return "".join(t.get("plain_text", "") for t in prop.get("rich_text", [])).strip()


def _title(prop):
    return "".join(t.get("plain_text", "") for t in prop.get("title", [])).strip()


def main():
    n = Client(auth=config.NOTION_API_KEY)
    db = n.databases.retrieve(database_id=config.NOTION_PARENT_ID)
    ds = db["data_sources"][0]["id"]

    pages, cursor = [], None
    while True:
        kw = {"data_source_id": ds, "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        resp = n.data_sources.query(**kw)
        pages += resp["results"]
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]

    placeholder = empty = ok = 0
    bad = []
    for pg in pages:
        p = pg["properties"]
        name = _title(p.get("公司", {}))
        summary = _rich(p.get("重點摘要", {}))
        ai_view = _rich(p.get("AI 觀點與未來方向分析", {}))
        date = ((p.get("日期", {}).get("date") or {}).get("start") or "")[:10]
        tag = f"{name} {date}"
        # 佔位特徵：摘要含「時間：」「地點：」且 ai_view 很短或空
        if ("時間：" in summary or "地點：" in summary) and len(ai_view) < 30:
            placeholder += 1
            bad.append(("佔位", tag))
        elif len(summary) < 20 or not ai_view:
            empty += 1
            bad.append(("空白", tag))
        else:
            ok += 1

    print(f"共 {len(pages)} 筆：正常 {ok}、佔位 {placeholder}、空白 {empty}")
    for kind, tag in bad[:40]:
        print(f"  [{kind}] {tag}")


if __name__ == "__main__":
    main()
