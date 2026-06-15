"""總經未來頁的 AI 分析（Gemini 生成，依即時市場數字）。

量極小（十餘個指標、一天一次），免費額度綽綽有餘，可在雲端 CI 自動跑。
嚴格要求只引用傳入的數字、不得杜撰；失敗時由 build_macro 退回手寫基準版。
"""
from datetime import date

from google import genai
from google.genai import types
from pydantic import BaseModel

import config
from ir.gemini_util import generate_with_retry
from ir.logger import get_logger

log = get_logger("ir.macro_ai")


class _MacroAI(BaseModel):
    overall: str
    equity_analysis: str
    equity_expectation: str
    bond_analysis: str
    realestate_analysis: str


_SYSTEM = (
    "你是資深的總體經濟與資產配置策略分析師。輸出嚴格符合 JSON。"
    "全部使用繁體中文（台灣用語與金融術語）。專業、客觀、精簡，不用 emoji。"
    "極重要：只能引用使用者提供的數字，嚴禁杜撰任何未提供的數據、事件或機構預測。"
    "每個欄位 3～5 句。"
)

_PROMPT = """以下是 {today} 的最新市場數據（你只能依據這些數字撰寫，不得自行編造其他數字）：

{blocks}

請輸出 JSON（不要 markdown 圍欄），五個欄位皆為純文字段落：
{{
 "overall":"總結當前股市／債市／房市的整體格局（3～4 句）",
 "equity_analysis":"股市現況分析，須對照標普500／那斯達克／台股加權／VIX 的數字與漲跌",
 "equity_expectation":"股市預期：方向研判（偏多／偏空／中性）、後續觀察重點、主要風險；結尾務必加上一句『本內容由 AI 依公開數據彙整，僅供研究參考，不構成投資建議。』",
 "bond_analysis":"債市分析，須對照各天期公債殖利率、殖利率曲線形狀與長債 ETF",
 "realestate_analysis":"房市分析，須對照房屋建商／REIT 的表現，並可連結公債殖利率對房貸利率的意涵"
}}"""


def _render_blocks(groups: list[dict], econ: list[dict]) -> str:
    lines = []
    for g in groups:
        lines.append(f"【{g['label']}】")
        for it in g["items"]:
            d = "，".join(f"{x['label']} {x['text']}" for x in it.get("deltas", []))
            unit = it.get("unit") or ""
            lines.append(f"- {it['name']}：{it['value']}{unit}"
                         + (f"（{d}）" if d else ""))
        lines.append("")
    if econ:
        lines.append("【經濟數據】")
        for e in econ:
            lines.append(f"- {e['name']}：{e['value']}{e.get('unit', '')}"
                         f"（{e.get('asof', '')}）")
    return "\n".join(lines).strip()


def generate_macro_ai(groups: list[dict], econ: list[dict]) -> dict:
    """回傳 {asof, ai:{overall, equity:{analysis,expectation}, bond:{analysis},
    realestate:{analysis}}}；無金鑰或全模型失敗時拋出例外，由呼叫端退回基準版。"""
    if not config.GEMINI_API_KEY:
        raise RuntimeError("未設定 GEMINI_API_KEY")

    prompt = _PROMPT.format(today=date.today().isoformat(),
                            blocks=_render_blocks(groups, econ))
    client = genai.Client(api_key=config.GEMINI_API_KEY,
                          http_options=types.HttpOptions(timeout=120_000))
    resp = generate_with_retry(
        client,
        contents=[prompt],
        config=types.GenerateContentConfig(
            temperature=0.3,
            system_instruction=_SYSTEM,
            response_mime_type="application/json",
            response_schema=_MacroAI,
        ),
    )
    r = resp.parsed
    log.info("總經 AI 已由 Gemini 生成")
    return {
        "asof": date.today().isoformat(),
        "ai": {
            "overall": r.overall,
            "equity": {"analysis": r.equity_analysis,
                       "expectation": r.equity_expectation},
            "bond": {"analysis": r.bond_analysis},
            "realestate": {"analysis": r.realestate_analysis},
        },
    }
