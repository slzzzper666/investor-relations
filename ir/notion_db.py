"""階段五(1)：寫入 Notion 資料庫（一場法說會一行）。

沿用使用者已建立的欄位：
  公司(title)、股票代號(number)、日期(date)、簡報(url)、YT(url)、
  逐字稿(rich_text)、重點摘要(rich_text)、AI 觀點與未來方向分析(rich_text)
同公司同日期重跑時會更新既有列，不會重複新增。
"""
from notion_client import Client

import config
from ir.logger import get_logger
from ir.mops import Conference

log = get_logger("ir.notion")

_client: Client | None = None
_ds_id: str | None = None


def _get() -> tuple[Client, str]:
    global _client, _ds_id
    if _client is None:
        _client = Client(auth=config.NOTION_API_KEY)
        db = _client.databases.retrieve(database_id=config.NOTION_PARENT_ID)
        _ds_id = db["data_sources"][0]["id"]
    return _client, _ds_id


def _rich_chunks(text: str, limit: int = 2000, max_chunks: int = 90) -> list[dict]:
    """Notion 單一 text 物件上限 2000 字，長文切塊。"""
    text = text.strip()
    chunks = [text[i:i + limit] for i in range(0, len(text), limit)][:max_chunks]
    return [{"type": "text", "text": {"content": c}} for c in chunks]


def _find_page(n: Client, ds_id: str, conf: Conference) -> dict | None:
    """同公司同日期的既有頁面（沒有回 None）。"""
    res = n.data_sources.query(
        data_source_id=ds_id,
        filter={"and": [
            {"property": "日期", "date": {"equals": conf.date.isoformat()}},
            {"property": "股票代號",
             "number": {"equals": int(conf.stock_code)}}
            if conf.stock_code.isdigit() else
            {"property": "公司", "title": {"equals": conf.company_name}},
        ]},
        page_size=1,
    )
    return res["results"][0] if res["results"] else None


def status(conf: Conference) -> dict | None:
    """這場法說會在 Notion 的狀態：{'id', 'has_transcript'}；不存在回 None。

    每日管線回補前幾天用：Railway 容器的 processed.json 是暫時性的，
    Notion 才是「有沒有做過」唯一可靠的依據。has_transcript 讓管線知道
    哪些場次還缺逐字稿（錄影通常比法說會晚幾小時到一天才上傳）。
    """
    n, ds_id = _get()
    page = _find_page(n, ds_id, conf)
    if page is None:
        return None
    pr = page.get("properties", {})
    rt = pr.get("逐字稿", {}).get("rich_text") or []
    sg = pr.get("分段", {}).get("rich_text") or []
    return {"id": page["id"], "has_transcript": bool(rt), "has_segments": bool(sg)}


def exists(conf: Conference) -> bool:
    return status(conf) is not None


def segments_to_text(plan: dict | None) -> str:
    """錨點檔 → 存進「分段」欄位的 JSON 字串（只存 segments，精簡到 2000 字內為佳）。"""
    if not plan or not plan.get("segments"):
        return ""
    import json
    return json.dumps({"v": plan.get("v", 1), "by": plan.get("by", "gemini"),
                       "segments": plan["segments"]}, ensure_ascii=False,
                      separators=(",", ":"))


def save_segments(conf: Conference, plan: dict | None) -> bool:
    """只更新「分段」欄位（逐字稿補段用）。頁面不存在回 False。"""
    text = segments_to_text(plan)
    if not text:
        return False
    n, ds_id = _get()
    page = _find_page(n, ds_id, conf)
    if page is None:
        return False
    n.pages.update(page_id=page["id"], properties={"分段": {"rich_text": _rich_chunks(text)}})
    log.info("Notion 分段已更新：%s %s（%d 段）", conf.stock_code, conf.company_name,
             len(plan["segments"]))
    return True


def upsert_conference(conf: Conference, analysis: dict, transcript: str,
                      video_url: str, segments: dict | None = None) -> str:
    """寫入/更新一列，回傳 Notion 頁面 URL。segments 為錨點檔（有逐字稿時可帶）。"""
    n, ds_id = _get()

    highlights = "\n".join(f"• {h}" for h in analysis.get("highlights", []))
    summary_text = f"{analysis.get('one_liner', '')}\n{highlights}"
    view_text = (f"{analysis.get('ai_view', '')}\n\n"
                 f"【展望】{analysis.get('outlook', '')}")

    props: dict = {
        "公司": {"title": [{"type": "text",
                            "text": {"content": f"{conf.company_name}"}}]},
        "日期": {"date": {"start": conf.date.isoformat()}},
        "重點摘要": {"rich_text": _rich_chunks(summary_text)},
        "AI 觀點與未來方向分析": {"rich_text": _rich_chunks(view_text)},
    }
    if conf.stock_code.isdigit():
        props["股票代號"] = {"number": int(conf.stock_code)}
    if conf.pdf_url:
        props["簡報"] = {"url": conf.pdf_url}
    if video_url:
        props["YT"] = {"url": video_url}
    if transcript:
        props["逐字稿"] = {"rich_text": _rich_chunks(transcript)}
        seg_text = segments_to_text(segments)
        if seg_text:
            props["分段"] = {"rich_text": _rich_chunks(seg_text)}

    # 同公司同日期 → 更新而非新增
    existing = _find_page(n, ds_id, conf)
    if existing:
        page = n.pages.update(page_id=existing["id"], properties=props)
        log.info("Notion 已更新：%s %s", conf.stock_code, conf.company_name)
    else:
        page = n.pages.create(
            parent={"type": "data_source_id", "data_source_id": ds_id},
            properties=props,
        )
        log.info("Notion 已新增：%s %s", conf.stock_code, conf.company_name)
    return page.get("url", "")
