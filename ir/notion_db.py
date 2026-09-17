"""階段五(1)：寫入 Notion 資料庫（一場法說會一行）。

沿用使用者已建立的欄位：
  公司(title)、股票代號(number)、日期(date)、簡報(url)、YT(url)、
  逐字稿(rich_text)、重點摘要(rich_text)、AI 觀點與未來方向分析(rich_text)
管線後加的欄位（皆 rich_text 存 JSON 字串）：
  分段：逐字稿分段錨點（ir/segment.py）
  比較：與同公司上一場法說會的對照（ir/compare.py）
同公司同日期重跑時會更新既有列，不會重複新增。
"""
import json

from notion_client import Client

import config
from ir.logger import get_logger
from ir.mops import Conference
from ir.season import MIN_GAP_DAYS, pick_previous, season_of

log = get_logger("ir.notion")

_client: Client | None = None
_ds_id: str | None = None
PREV_SCAN = 12      # 往回掃幾場找「上一季那場」（同一季最多看過一家開 12 場）


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


def _plain(prop: dict) -> str:
    """rich_text 屬性 → 純文字（查詢回應每個屬性最多帶 25 段 = 5 萬字，比較用途足夠）。"""
    return "".join(t.get("plain_text", "") for t in prop.get("rich_text") or [])


def analysis_texts(analysis: dict) -> tuple[str, str]:
    """分析結果 → (重點摘要, AI 觀點) 兩段文字；寫 Notion 與「比較」的輸入都用同一格式。"""
    highlights = "\n".join(f"• {h}" for h in analysis.get("highlights", []))
    summary_text = f"{analysis.get('one_liner', '')}\n{highlights}"
    view_text = (f"{analysis.get('ai_view', '')}\n\n"
                 f"【展望】{analysis.get('outlook', '')}")
    return summary_text, view_text


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
    cp = pr.get("比較", {}).get("rich_text") or []
    return {"id": page["id"], "has_transcript": bool(rt), "has_segments": bool(sg),
            "has_compare": bool(cp)}


def exists(conf: Conference) -> bool:
    return status(conf) is not None


def previous_conference(conf: Conference) -> dict | None:
    """同公司「上一季」的最後一場：{id, date, season, summary, ai_view, transcript}；沒有回 None。

    「與上次法說會比較」的輸入。同一季公司常開好幾場（自辦＋券商協辦），彼此講的是同一份
    財報，比了等於沒比——所以往回掃 PREV_SCAN 場，挑第一個「財報季不同且相隔夠久」的
    （見 ir/season）。逐字稿只取查詢回應帶回的前段（ir/compare 只用前 6000 字）。
    """
    if not conf.stock_code.isdigit():
        return None
    n, ds_id = _get()
    res = n.data_sources.query(
        data_source_id=ds_id,
        filter={"and": [
            {"property": "股票代號", "number": {"equals": int(conf.stock_code)}},
            {"property": "日期", "date": {"before": conf.date.isoformat()}},
        ]},
        sorts=[{"property": "日期", "direction": "descending"}],
        page_size=PREV_SCAN,
    )
    rows = [{"page": p,
             "date": ((p.get("properties", {}).get("日期", {}).get("date") or {}).get("start") or "")[:10]}
            for p in res["results"]]
    hit = pick_previous(conf.date, rows)
    if not hit:
        return None
    pr = hit["page"].get("properties", {})
    return {"id": hit["page"]["id"], "date": hit["date"], "season": season_of(hit["date"]),
            "summary": _plain(pr.get("重點摘要", {})),
            "ai_view": _plain(pr.get("AI 觀點與未來方向分析", {})),
            "transcript": _plain(pr.get("逐字稿", {}))}


def compare_to_text(result: dict | None) -> str:
    """比較結果 → 存進「比較」欄位的 JSON 字串。"""
    if not result or not result.get("items"):
        return ""
    keep = {k: result[k] for k in ("prev_date", "prev_id", "prev_season", "cur_season",
                                   "verdict", "items", "watch", "model") if k in result}
    return json.dumps(keep, ensure_ascii=False, separators=(",", ":"))


def save_compare(conf: Conference, result: dict | None) -> bool:
    """只更新「比較」欄位（回補用）。頁面不存在或沒結果回 False。"""
    text = compare_to_text(result)
    if not text:
        return False
    n, ds_id = _get()
    page = _find_page(n, ds_id, conf)
    if page is None:
        return False
    n.pages.update(page_id=page["id"], properties={"比較": {"rich_text": _rich_chunks(text)}})
    log.info("Notion 比較已更新：%s %s（vs %s，%d 項）", conf.stock_code, conf.company_name,
             result.get("prev_date", ""), len(result["items"]))
    return True


def segments_to_text(plan: dict | None) -> str:
    """錨點檔 → 存進「分段」欄位的 JSON 字串（只存 segments，精簡到 2000 字內為佳）。"""
    if not plan or not plan.get("segments"):
        return ""
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
                      video_url: str, segments: dict | None = None,
                      compare: dict | None = None) -> str:
    """寫入/更新一列，回傳 Notion 頁面 URL。

    segments 為分段錨點檔（有逐字稿時可帶）；compare 為與上一場的比較結果。
    """
    n, ds_id = _get()
    summary_text, view_text = analysis_texts(analysis)

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
    cmp_text = compare_to_text(compare)
    if cmp_text:
        props["比較"] = {"rich_text": _rich_chunks(cmp_text)}

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
