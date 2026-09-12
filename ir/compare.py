"""與上次法說會比較：兩場法說會的重點對照（Gemini）。

單場摘要網路上到處都有，「跨時間追蹤」才是差異化：上次說要擴產 50%，這次進度？
毛利率指引從 58% 上修到 60%？哪個議題上次沒提、這次突然出現？

輸入用兩場的摘要與 AI 觀點（都是站上既有資料），有逐字稿再各附前 6000 字當佐證。
嚴禁引用未提供的內容；沒有可比的就回空陣列，不硬湊。
"""
import json

from google.genai import types
from pydantic import BaseModel, Field

from ir.gemini_util import all_exhausted, generate_with_retry
from ir.logger import get_logger

log = get_logger("ir.compare")

_TRANSCRIPT_CAP = 6000


class _Item(BaseModel):
    topic: str          # 議題（8 字內）
    before: str         # 上次怎麼說（30 字內；沒提填空）
    after: str          # 這次怎麼說（30 字內）
    direction: str      # up｜down｜same｜new｜gone


class _Compare(BaseModel):
    verdict: str = ""                                   # 一句話：這次相對上次的整體基調
    items: list[_Item] = Field(default_factory=list)    # 逐項對照（3～8 項）
    watch: list[str] = Field(default_factory=list)      # 下次法說會要追蹤的 1～3 點


_SYSTEM = (
    "你是資深台股產業分析師，專長是追蹤同一家公司跨季法說會的口徑變化。"
    "只能根據提供的兩場資料比較，嚴禁引用未出現的數字或事件；"
    "沒有可比的項目就少寫，不要硬湊。輸出繁體中文（台灣用語），不用 emoji。"
)

_PROMPT = """{company}（{code}）兩場法說會的對照。

【上次：{prev_date}】
重點摘要：
{prev_summary}
AI 觀點：
{prev_view}
{prev_transcript}
【這次：{cur_date}】
重點摘要：
{cur_summary}
AI 觀點：
{cur_view}
{cur_transcript}
請輸出 JSON（不要 markdown code fence）：
{{
 "verdict": "一句話（40 字內）：這次相對上次的整體基調變化，例如『展望由保守轉樂觀，AI 訂單能見度拉長』",
 "items": [
   {{"topic": "議題名（8 字內）", "before": "上次怎麼說（30 字內，含數字）", "after": "這次怎麼說（30 字內，含數字）",
     "direction": "up=上修/轉佳、down=下修/轉弱、same=維持、new=這次新出現、gone=這次不再提"}}
 ],
 "watch": ["下次法說會要追蹤的重點（各 25 字內）"]
}}
items 3～8 項，優先放：財測／毛利率指引、產能與資本支出、訂單能見度、主要產品線動能、
上次承諾的事這次的進度。before/after 盡量帶原文數字。"""


def _tr(t: str, label: str) -> str:
    t = (t or "").strip()
    if not t:
        return ""
    return f"逐字稿（前 {_TRANSCRIPT_CAP} 字）：\n{t[:_TRANSCRIPT_CAP]}\n"


def compare(company: str, code: str, cur: dict, prev: dict) -> dict | None:
    """cur/prev: {date, summary, ai_view, transcript?}。回 {prev_date, verdict, items, watch}。"""
    if all_exhausted():
        raise RuntimeError("Gemini 今日額度已耗盡")
    if not (prev.get("summary") and cur.get("summary")):
        return None
    prompt = _PROMPT.format(
        company=company, code=code,
        prev_date=prev["date"], prev_summary=prev["summary"], prev_view=prev.get("ai_view", ""),
        prev_transcript=_tr(prev.get("transcript", ""), "上次"),
        cur_date=cur["date"], cur_summary=cur["summary"], cur_view=cur.get("ai_view", ""),
        cur_transcript=_tr(cur.get("transcript", ""), "這次"),
    )
    resp = generate_with_retry(
        [prompt], timeout_ms=180_000,
        config_=types.GenerateContentConfig(
            temperature=0.2, system_instruction=_SYSTEM,
            response_mime_type="application/json", response_schema=_Compare),
    )
    parsed = resp.parsed
    if not isinstance(parsed, _Compare):
        parsed = _Compare.model_validate(json.loads(resp.text))
    items = [i.model_dump() for i in parsed.items
             if i.topic.strip() and (i.before.strip() or i.after.strip())]
    for i in items:
        if i["direction"] not in ("up", "down", "same", "new", "gone"):
            i["direction"] = "same"
    if not items:
        return None
    log.info("比較 %s %s：%s vs %s → %d 項（%s）", code, company, cur["date"], prev["date"],
             len(items), getattr(resp, "model_version", ""))
    return {"prev_date": prev["date"], "prev_id": prev.get("id", ""),
            "verdict": parsed.verdict.strip(), "items": items,
            "watch": [w.strip() for w in parsed.watch if w.strip()][:3],
            "model": getattr(resp, "model_version", "") or ""}
