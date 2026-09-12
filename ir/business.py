"""業務項目萃取：從法說會簡報 PDF／逐字稿抽出「產品或業務別營收比重」。

比重數字多半畫在簡報的圓餅圖／堆疊長條圖裡，純文字抽取拿不到，
因此把 PDF 原檔交給 Gemini（多模態，看得懂圖表）；沒有 PDF 才退用逐字稿文字。

輸出兩層：
  segments  公司自己的講法（如「GaAs 功率放大器」62%），保留原味供閱讀
  tags      正規化族群標籤（限定 TAXONOMY 內），供跨公司比對與首頁篩選

族群標籤一定要收斂到固定字彙，否則「ABF載板／ABF 載板／IC載板」會變成三個
不同族群，跨公司連結就失效了。
"""
import json
import re
from pathlib import Path

from google.genai import types
from pydantic import BaseModel, Field

from ir.gemini_util import all_exhausted, generate_with_retry
from ir.logger import get_logger

log = get_logger("ir.business")

# ── 族群字彙（AI 只能從這裡挑；涵蓋台股主要題材）────────────
TAXONOMY = [
    # 半導體
    "晶圓代工", "IC設計", "記憶體", "封裝測試", "先進封裝/CoWoS", "ABF載板",
    "IC基板", "半導體設備", "半導體材料", "矽智財IP", "化合物半導體", "功率半導體",
    # 電子零組件／PCB
    "PCB", "銅箔基板", "被動元件", "連接器", "電源供應器", "散熱", "機殼結構件",
    "石英元件", "電池模組",
    # 系統與網通
    "伺服器", "AI伺服器", "筆電/PC", "手機", "網通設備", "光通訊", "衛星通訊",
    "工業電腦", "車用電子", "電動車", "自動化/機器人", "無人機",
    # 光電顯示
    "面板", "面板零組件", "LED", "光學鏡頭", "感測元件", "微型顯示器",
    # 軟體服務
    "資訊服務", "軟體/SaaS", "電商", "遊戲", "數位金融", "資安", "雲端服務",
    # 傳產
    "鋼鐵", "水泥", "塑化", "化工", "紡織", "橡膠輪胎", "造紙", "玻璃陶瓷",
    "食品", "農業科技", "汽車零組件", "工具機", "機械設備", "手工具",
    "建材", "營建", "資產開發",
    # 能源與環境
    "太陽能", "風電", "儲能", "電力設備", "環保工程", "水處理",
    # 服務與金融
    "航運", "航空", "貨運物流", "觀光旅館", "餐飲", "零售通路", "百貨",
    "銀行", "保險", "證券", "租賃", "金控",
    # 生醫
    "新藥研發", "學名藥", "原料藥", "醫療器材", "醫療通路", "健康檢測", "美妝保健",
    # 美股常見（台股少見）族群：字彙與台股共用，同一標籤兩市場都能篩
    "社群媒體/廣告", "串流媒體", "支付", "航太國防", "油氣", "公用事業", "電信",
    "資產管理", "消費品牌", "連鎖餐飲", "運動用品", "醫療保險", "GPU/AI晶片",
    # 其他
    "其他",
]

_SYSTEM = (
    "你是台股產業分析師，專門從法說會資料萃取公司的業務組合。"
    "只根據提供的資料作答，嚴禁杜撰任何未出現的數字或業務項目。"
    "輸出一律繁體中文（台灣用語），不使用 emoji。"
)

_PROMPT = """這是 {company}（{code}）的法人說明會資料。

請萃取這家公司的「業務／產品別營收比重」。注意：
1. 比重數字常畫在圓餅圖、堆疊長條圖或表格中，請仔細判讀圖表。
2. 只取「營收（或銷售額）結構佔比」，不要取毛利率、成長率、良率、持股比例、
   股利配發率這類與營收結構無關的百分比。
3. 若資料中同時有多期，取「最新一期」的比重，並在 as_of 註明期別。
4. 如果資料裡完全找不到營收結構，segments 回空陣列、confidence 填 "none"。
   寧可空手，也不要用產業常識推測。

再從下列固定清單挑 1～4 個最能代表這家公司的族群標籤（**只能用清單內的字**）：
{taxonomy}

輸出 JSON（不要 markdown code fence）：
{{
  "segments": [
    {{"name": "業務項目名稱（用公司自己的講法，12字內）",
      "pct": 佔比數字（0~100，沒寫明就填 null）,
      "note": "補充說明，20字內，沒有就空字串"}}
  ],
  "as_of": "比重所屬期別，如 2026Q2 或 2026H1；不確定填空字串",
  "tags": ["族群標籤"],
  "confidence": "high｜medium｜low｜none"
}}

{content}"""


class Segment(BaseModel):
    name: str
    pct: float | None = None
    note: str = ""


class BusinessProfile(BaseModel):
    segments: list[Segment] = Field(default_factory=list)
    as_of: str = ""
    tags: list[str] = Field(default_factory=list)
    confidence: str = "none"


MAX_INLINE_PDF = 18 * 1024 * 1024   # 單次請求上限約 20MB，留安全邊際


_SEASON_WORDS = {"一": 1, "二": 2, "三": 3, "四": 4,
                 "1": 1, "2": 2, "3": 3, "4": 4}


def _norm_period(raw: str) -> str:
    """期別正規化：'115年上半年'、'2026 H1'、'2026年第二季' → '2026 H1' / '2026 Q2'。

    AI 講法不統一（民國/西元、中文/英文），不收斂的話前端會出現三種寫法。
    """
    t = raw.strip()
    if not t:
        return ""
    m = re.search(r"(19|20)\d{2}", t)
    if m:
        year = int(m.group(0))
    else:
        m = re.search(r"(?<!\d)(1\d{2})\s*年", t)     # 民國年（前面可能接「民國」）
        if not m:
            return ""
        year = int(m.group(1)) + 1911

    if re.search(r"H1|上半年", t, re.IGNORECASE):
        return f"{year} H1"
    if re.search(r"H2|下半年", t, re.IGNORECASE):
        return f"{year} H2"
    m = re.search(r"Q([1-4])", t, re.IGNORECASE)
    if m:
        return f"{year} Q{m.group(1)}"
    m = re.search(r"第([一二三四1234])季", t)
    if m:
        return f"{year} Q{_SEASON_WORDS[m.group(1)]}"
    if re.search(r"全年|年度", t):
        return f"{year} 全年"
    return str(year)


def _clean(profile: BusinessProfile) -> dict:
    """收斂 AI 輸出：標籤限定字彙、比重合理化、依佔比排序。"""
    tags, seen = [], set()
    for t in profile.tags:
        t = t.strip()
        if t in TAXONOMY and t not in seen:
            seen.add(t)
            tags.append(t)
    segs = []
    for s in profile.segments:
        name = s.name.strip()
        if not name:
            continue
        pct = s.pct
        if pct is not None and not (0 <= pct <= 100):
            pct = None
        segs.append({"name": name, "pct": pct, "note": s.note.strip()})

    # 合理性檢查：營收結構是一組互斥項目，合計不可能超過 100%（容忍四捨五入）。
    # 實測有的簡報只畫「各產品線成長率」沒畫佔比，模型會誤把成長率當佔比抓下來
    # （症狀＝項目名重複、合計遠超過 100%）。寧可整組丟掉只留族群標籤，
    # 也不要把錯的數字放上站——讀者無從分辨對錯。
    # 只管上限不管下限：簡報常只列主要幾項，合計偏低是合法的。
    if segs:
        names = [s["name"] for s in segs]
        total = sum(s["pct"] for s in segs if s["pct"] is not None)
        dup = len(names) != len(set(names))
        if dup or total > 115:
            log.warning("業務項目合理性檢查未過（%d 項、合計 %.0f%%、名稱重複 %s），"
                        "捨棄比重只保留族群", len(segs), total, dup)
            segs = []
            profile.confidence = "none"

    # 有佔比的排前面（大→小），沒佔比的維持原順序墊後
    segs.sort(key=lambda x: (x["pct"] is None, -(x["pct"] or 0)))
    return {
        "segments": segs,
        "as_of": _norm_period(profile.as_of),
        "tags": tags[:4],          # 版面固定：模型偶爾會多給，一律取最相關的前 4 個
        "confidence": profile.confidence.strip().lower() or "none",
    }


def extract(company: str, code: str, pdf_path: Path | None = None,
            transcript: str = "") -> dict:
    """回傳 {segments, as_of, tags, confidence}。無可用來源時丟 ValueError。"""
    if pdf_path is None and not transcript:
        raise ValueError("pdf_path 與 transcript 至少要有一個")
    if all_exhausted():
        raise RuntimeError("Gemini 今日額度已耗盡")

    taxonomy = "、".join(TAXONOMY)
    if pdf_path is not None:
        # PDF 直接 inline：不走 Files API，就沒有「上傳的檔案綁定該金鑰」的問題，
        # 多金鑰輪替時不必為了換金鑰重新上傳，也省一趟往返。
        data = pdf_path.read_bytes()
        if len(data) > MAX_INLINE_PDF:
            raise ValueError(f"簡報過大（{len(data) / 1e6:.1f}MB），超出單次請求上限")
        prompt = _PROMPT.format(company=company, code=code, taxonomy=taxonomy,
                                content="資料見所附簡報 PDF。")
        contents: list = [
            types.Part.from_bytes(data=data, mime_type="application/pdf"),
            prompt,
        ]
    else:
        prompt = _PROMPT.format(company=company, code=code, taxonomy=taxonomy,
                                content="法說會逐字稿如下：\n" + transcript[:30000])
        contents = [prompt]

    resp = generate_with_retry(
        contents,
        config_=types.GenerateContentConfig(
            temperature=0.1,
            system_instruction=_SYSTEM,
            response_mime_type="application/json",
            response_schema=BusinessProfile,
        ),
    )
    parsed = resp.parsed
    if not isinstance(parsed, BusinessProfile):
        parsed = BusinessProfile.model_validate(json.loads(resp.text))

    out = _clean(parsed)
    # 記下實際產出的模型：降級鏈會依額度自動換模型，出問題時要查得到是誰做的
    out["model"] = getattr(resp, "model_version", "") or ""
    log.info("業務項目 %s %s：%d 項、標籤 %s（%s｜%s）", code, company,
             len(out["segments"]), "/".join(out["tags"]) or "無",
             out["confidence"], out["model"] or "未知模型")
    return out
