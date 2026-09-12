"""逐字稿自動分段（Gemini）：產出與人工版相同格式的「錨點檔」。

錨點檔 data/segments/{id}.json：
  {"id", "v", "by": "gemini", "segments": [{"type": intro|topic|qa, "title", "start"}]}
start 是逐字稿中該段開頭的一小段**原文**（不含逗號、20～40 字），build_data 用
find() 定位切點——所以模型只需輸出錨點、不必回吐整篇，省 token 也不會改到原文。

驗證：每個 start 一定要能在逐字稿裡 find 得到（台/臺正規化後），對不上的丟掉；
剩不到 3 段就視為失敗不存檔（等於沒分段，前端退回整篇顯示）。
"""
import json
import re

from google.genai import types
from pydantic import BaseModel, Field

from ir.gemini_util import all_exhausted, generate_with_retry
from ir.logger import get_logger

log = get_logger("ir.segment")

MIN_SEGMENTS = 3
MAX_CHARS = 60000     # 逐字稿超長就截尾（分段錨點集中在前段與 QA 開頭）


class _Anchor(BaseModel):
    type: str
    title: str
    start: str


class _Plan(BaseModel):
    segments: list[_Anchor] = Field(default_factory=list)


_SYSTEM = (
    "你是法說會逐字稿的結構編輯。任務是替逐字稿標出段落切點，"
    "輸出一律繁體中文標題；start 欄位必須一字不改地抄自逐字稿原文。"
)

_PROMPT = """以下是 {company}（{code}）{date} 法說會的逐字稿。請把它切成有意義的段落：

規則：
1. 前段依主題切（type="topic"）：財務摘要、各產品線、展望、資本支出、股利等，
   每段 title 為 6～16 字的繁體中文小標。
2. 問答（Q&A）開始後，每一題一段（type="qa"），title 寫成該題的問題摘要（16 字內，
   可含提問機構）。
3. 每段的 start 必須是**該段開頭的原文**，長度 20～40 字、不要含逗號或換行、
   一字不改（含英文、數字、標點）——系統會用它在原文裡定位，改一個字就對不上。
4. 段數 6～30 段。開場致詞不必獨立一段（系統會自動當 intro）。
5. 不要輸出段落內容，只輸出錨點。

輸出 JSON（不要 markdown code fence）：
{{"segments":[{{"type":"topic","title":"…","start":"…"}}, …]}}

逐字稿：
{transcript}"""


def _norm(s: str) -> str:
    return s.replace("臺", "台")


def plan_segments(company: str, code: str, conf_date: str, transcript: str) -> dict | None:
    """回錨點檔內容（dict）；分不出來回 None。額度耗盡丟 RuntimeError。"""
    if len(transcript) < 800:
        return None
    if all_exhausted():
        raise RuntimeError("Gemini 今日額度已耗盡")

    prompt = _PROMPT.format(company=company, code=code, date=conf_date,
                            transcript=transcript[:MAX_CHARS])
    resp = generate_with_retry(
        [prompt], timeout_ms=180_000,
        config_=types.GenerateContentConfig(
            temperature=0.1, system_instruction=_SYSTEM,
            response_mime_type="application/json", response_schema=_Plan),
    )
    parsed = resp.parsed
    if not isinstance(parsed, _Plan):
        parsed = _Plan.model_validate(json.loads(resp.text))

    ntrans = _norm(transcript)
    kept, last_pos = [], -1
    for a in parsed.segments:
        start = re.sub(r"\s+", " ", a.start).strip()
        if not start:
            continue
        pos = ntrans.find(_norm(start))
        if pos < 0 and len(start) > 24:          # 模型偶爾多抄或少抄尾巴：退用前 24 字再找
            pos = ntrans.find(_norm(start[:24]))
            start = start[:24]
        if pos < 0 or pos <= last_pos:           # 對不上、或順序倒退的丟掉
            continue
        t = a.type if a.type in ("topic", "qa", "intro") else "topic"
        kept.append({"type": t, "title": a.title.strip()[:24], "start": start})
        last_pos = pos

    if len(kept) < MIN_SEGMENTS:
        log.info("分段 %s %s：只對上 %d 段，放棄", code, company, len(kept))
        return None
    log.info("分段 %s %s：%d 段（模型給 %d、對上 %d｜%s）", code, company, len(kept),
             len(parsed.segments), len(kept), getattr(resp, "model_version", ""))
    return {"id": f"{code}_{conf_date}", "v": 1, "by": "gemini",
            "model": getattr(resp, "model_version", "") or "", "segments": kept}
