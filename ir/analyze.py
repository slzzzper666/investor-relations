"""階段四：AI 分析與總結（Gemini）。

輸入逐字稿（有影音）或簡報 PDF（無影音時的降級來源），
輸出結構化的重點摘要 + AI 觀點與未來方向分析。
"""
import time
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import BaseModel

import config
from ir.gemini_util import all_exhausted, generate_with_retry
from ir.logger import get_logger

log = get_logger("ir.analyze")

_SYSTEM = (
    "你是資深台股產業分析師，擅長從法說會內容萃取投資人關心的重點。"
    "輸出一律使用繁體中文（台灣用語），語氣專業精煉，不要出現任何 emoji。"
)

_PROMPT = """以下是 {company}（{code}）於 {date} 法人說明會的{source_desc}。

請輸出 JSON（不要 markdown code fence），格式：
{{
  "one_liner": "一句話總結這場法說會（60字內）",
  "highlights": ["重點摘要，5~8 條，每條一句話，涵蓋：營運/財務數字、產品與市場、訂單能見度、資本支出、股利政策（若有提及）"],
  "outlook": "公司展望與管理層說法的整理（150字內）",
  "ai_view": "你的 AI 觀點：這場法說會透露的訊號、潛在機會與風險、值得追蹤的指標（200字內）"
}}

內容如下：
{content}"""


class AnalysisResult(BaseModel):
    one_liner: str
    highlights: list[str]
    outlook: str
    ai_view: str


class AnalysisQuotaExhausted(Exception):
    """Gemini 與 Groq 當日免費額度都用盡（逐字稿已備妥，分析延後）。"""


class _GroqRateLimited(Exception):
    pass


def _client() -> genai.Client:
    return genai.Client(api_key=config.GEMINI_API_KEY,
                        http_options=types.HttpOptions(timeout=600_000))


def _pdf_text(pdf_path: Path) -> str:
    """擷取 PDF 文字（給 Groq 備援用；掃描型 PDF 可能擷取不到）。"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        return "\n".join(p.extract_text() or "" for p in reader.pages).strip()
    except Exception as e:
        log.warning("PDF 文字擷取失敗 %s：%s", pdf_path, e)
        return ""


def analyze(company: str, code: str, conf_date: str,
            transcript: str = "", pdf_path: Path | None = None) -> dict:
    """回傳 {one_liner, highlights, outlook, ai_view}。失敗丟例外。

    逐字稿或 PDF 皆可；Gemini 為主、Groq llama 為備援（兩種來源都支援）。
    """
    if transcript:
        source_desc = "完整逐字稿"
        fallback_text = transcript
    elif pdf_path is not None:
        source_desc = "簡報資料（PDF，無影音逐字稿）"
        fallback_text = _pdf_text(pdf_path)
    else:
        raise ValueError("transcript 與 pdf_path 至少要有一個")

    groq_prompt = _PROMPT.format(company=company, code=code, date=conf_date,
                                 source_desc=source_desc,
                                 content=fallback_text[:30000])

    # Gemini 全部模型今日已耗盡 → 直接走 Groq，不浪費上傳
    if not all_exhausted():
        try:
            return _analyze_gemini(company, code, conf_date, source_desc,
                                   transcript, pdf_path)
        except Exception as e:
            if not (config.GROQ_API_KEY and fallback_text):
                raise
            log.warning("Gemini 分析失敗（%s），改用 Groq llama", e)
    elif not (config.GROQ_API_KEY and fallback_text):
        raise RuntimeError("Gemini 額度耗盡且無 Groq 備援可用")

    try:
        return _analyze_groq(groq_prompt)
    except _GroqRateLimited as e:
        raise AnalysisQuotaExhausted(str(e))


def _analyze_gemini(company: str, code: str, conf_date: str, source_desc: str,
                    transcript: str, pdf_path: Path | None) -> dict:
    client = _client()
    if transcript:
        prompt = _PROMPT.format(company=company, code=code, date=conf_date,
                                source_desc=source_desc, content=transcript)
        contents: list = [prompt]
    else:
        prompt = _PROMPT.format(company=company, code=code, date=conf_date,
                                source_desc=source_desc, content="（見附件 PDF）")
        f = client.files.upload(file=str(pdf_path))
        while f.state and f.state.name == "PROCESSING":
            time.sleep(3)
            f = client.files.get(name=f.name)
        contents = [f, prompt]

    resp = generate_with_retry(
        client,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.3,
            system_instruction=_SYSTEM,
            response_mime_type="application/json",
            response_schema=AnalysisResult,
        ),
    )
    parsed = resp.parsed
    if not isinstance(parsed, AnalysisResult):
        raise RuntimeError("AI 分析輸出無法解析")
    log.info("AI 分析完成：%s %s（來源：%s）", code, company, source_desc)
    return parsed.model_dump()


def _analyze_groq(prompt: str) -> dict:
    import json
    import time as _t

    import requests

    for attempt in range(4):
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": _SYSTEM + " 只輸出合法 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
            },
            timeout=300,
        )
        if r.status_code == 429:
            ra = float(r.headers.get("retry-after", 30))
            if ra > 120:  # retry-after 很大＝當日額度耗盡，別等，直接放棄
                raise _GroqRateLimited(f"Groq 當日額度耗盡（retry-after {ra:.0f}s）")
            wait = min(ra, 90) + 1
            log.warning("Groq 限流，%ss 後重試（第 %d 次）", wait, attempt + 1)
            _t.sleep(wait)
            continue
        r.raise_for_status()
        result = json.loads(r.json()["choices"][0]["message"]["content"])
        parsed = AnalysisResult.model_validate(result)
        log.info("AI 分析完成（Groq llama 備援）")
        return parsed.model_dump()
    raise _GroqRateLimited("Groq llama 連續限流")
