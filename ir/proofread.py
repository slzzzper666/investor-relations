"""逐字稿文字校對：AI 只回「修正清單」，由程式套用——可追溯、不改寫全文。

做法（2026-10 矽格／台星科實測：套用 112 筆，「系格」「台新科」「法術會」全部歸零）：
  global：全文一律替換的錯詞（反覆出現的公司名錯字等）
  local ：帶前後文的單點修正，只換第一處
程式端的安全閥（AI 會猜錯，例如把「主計長」改成「財務長」）：
  1. 修正前後的數字不一致 → 不套用，列入 held（等人確認）。數字是財報逐字稿最不能錯的東西。
  2. 套用後任何既有公司名稱的出現次數變少 → 不套用（避免「台新」→「台星」誤傷「台新金」）。
  3. 原詞不在稿子裡、或原詞＝新詞 → 略過。
分段錨點（ir/segment 的 start 片段）同步套用同一批替換，再驗證還找得到。

同音候選：AI 校對時常只回「系格公司→矽格公司」這種長片段，單獨出現的「系格」漏掉。
所以先用拼音把「與本場公司名稱同音」的詞找出來（含台灣口音 in/ing、zh/z 等不分），
連同出現次數交給 AI 依上下文判斷——不由程式硬換，因為「統一」的法說會裡「同一」也同音。
"""
import json
import re
from functools import lru_cache

import requests
from google.genai import types
from ir.gemini_util import MODEL_CHAIN, exhausted_for, generate_with_retry
from ir.logger import get_logger
from ir.stt_hints import SITE, _company

log = get_logger("ir.proofread")


# 校對只用非 lite 模型：實測 lite 會漏改（法術會、主機長）且判斷較差；
# 額度用完寧可隔天再做，也不要低品質的修改寫進逐字稿
PROOF_MODELS = [m for m in MODEL_CHAIN if "lite" not in m]

_SYSTEM = ("你是台灣財經逐字稿的校對員，專門找出語音辨識造成的同音、近音錯字。"
           "只回傳修正清單 JSON，不改寫、不潤飾、不加標點。")

_PROMPT = """以下是台灣上市櫃公司法說會的語音辨識逐字稿（{date}，{who}），可能有同音／近音錯字。
請找出「語音辨識造成的錯字」，只回傳 JSON：{{"global":[...],"local":[...]}}
- global：全文都該一律替換的錯詞（例如反覆出現的公司名錯字），每筆 {{"wrong":"錯詞","right":"正確"}}；
  wrong 要夠長、不會誤傷其他詞
- local：只在特定位置的錯字，每筆 {{"wrong":"原文片段（含前後 2~4 字，讓它在全文唯一）","right":"修正後的同一片段"}}

規則：
- 只改明顯的同音、近音錯字（公司名、人名、產品、財報術語、一般用語）。
- 不改數字、年份、百分比；不潤飾語氣、不加標點、不刪字。
- 依上下文判斷：講到公司時才改成公司名；只是發音相近的一般詞彙不要硬套公司名。
- 職稱、人名不確定就不要改（例如「主計長」不要猜成「財務長」）。
- 不確定就不要改。沒有錯字就回 {{"global":[],"local":[]}}。

本場資料：
{ctx}
{homo}
台股上市櫃公司名稱（對照用）：{all}

逐字稿：
{text}"""


@lru_cache(maxsize=1)
def company_names() -> tuple[str, ...]:
    """全部台股公司名稱（去掉 * 與 -KY 等尾綴的乾淨名）。讀網站 list.json，失敗退本機。"""
    items = []
    try:
        r = requests.get(f"{SITE}list.json", timeout=30)
        items = r.json().get("items", [])
    except (requests.RequestException, ValueError):
        import config
        f = config.BASE_DIR / "site" / "public" / "list.json"
        if f.exists():
            items = json.loads(f.read_text(encoding="utf-8")).get("items", [])
    names = {re.sub(r"[*＊]$", "", x.get("company", "")).strip() for x in items}
    return tuple(sorted(n for n in names if len(n) >= 2))


def _ctx(company: str, code: str) -> str:
    biz = _company(code) if code and code.isdigit() else {}
    segs = "、".join(s.get("name", "") for s in biz.get("segments") or [])
    tags = "、".join(biz.get("tags") or [])
    return f"公司：{company}（{code}）\n業務：{segs or '—'}\n族群：{tags or '—'}"


_FUZZY = (("zh", "z"), ("ch", "c"), ("sh", "s"), ("ing", "in"), ("eng", "en"), ("ang", "an"))


def _key(py: str) -> str:
    for a, b in _FUZZY:
        py = py.replace(a, b)
    return py


def homophone_candidates(text: str, names: list[str], min_count: int = 2) -> dict[str, tuple[int, str]]:
    """逐字稿中與 names 同音（模糊拼音）但字不同的詞 → (出現次數, 音同哪個名稱)。"""
    from pypinyin import lazy_pinyin
    chars = list(text)
    keys = [_key(p) if re.match(r"[\u4e00-\u9fff]", c) else "" for c, p in
            zip(chars, lazy_pinyin(chars, errors=lambda x: [""] * len(x)))]
    known = set(company_names())
    out: dict[str, int] = {}
    src: dict[str, str] = {}
    for name in names:
        if not (2 <= len(name) <= 4) or not re.fullmatch(r"[\u4e00-\u9fff]+", name):
            continue
        target = [_key(p) for p in lazy_pinyin(name)]
        n = len(name)
        for i in range(len(chars) - n + 1):
            if keys[i:i + n] == target:
                w = "".join(chars[i:i + n])
                if w != name and w not in known:
                    out[w] = out.get(w, 0) + 1
                    src[w] = name
    return {w: (c, src[w]) for w, c in sorted(out.items(), key=lambda x: -x[1]) if c >= min_count}


def _digits(s: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", s)


def apply_fixes(text: str, fixes: dict, names: tuple[str, ...] = ()) -> tuple[str, list, list, list]:
    """套用修正清單。回 (新稿, applied, held, rejected)；applied 依套用順序，可重播到錨點上。"""
    out, applied, held, rejected = text, [], [], []
    present = [n for n in names if n in text]

    def safe(before: str, after: str) -> bool:
        return all(after.count(n) >= before.count(n) for n in present)

    for kind in ("global", "local"):
        for f in fixes.get(kind) or []:
            w, r = (f.get("wrong") or "").strip(), (f.get("right") or "").strip()
            if len(w) < 2 or not r or w == r or w not in out:
                continue
            rec = {"kind": kind, "wrong": w, "right": r}
            if _digits(w) != _digits(r):
                held.append({**rec, "reason": "修正會改到數字"})
                continue
            cand = out.replace(w, r) if kind == "global" else out.replace(w, r, 1)
            if not safe(out, cand):
                rejected.append({**rec, "reason": "會讓既有公司名稱減少"})
                continue
            rec["n"] = out.count(w) if kind == "global" else 1
            out = cand
            applied.append(rec)
    return out, applied, held, rejected


def _norm(s: str) -> str:
    return s.replace("臺", "台")


def replay_on_segments(plan: dict | None, applied: list, new_text: str) -> tuple[dict | None, int]:
    """把同一批替換套到分段錨點上，再驗證每個錨點仍找得到。回 (新錨點檔或 None, 掉了幾個)。"""
    if not plan or not plan.get("segments"):
        return plan, 0
    nt = _norm(new_text)
    kept, lost, last = [], 0, -1
    for s in plan["segments"]:
        start = s.get("start", "")
        for a in applied:
            if a["wrong"] in start:
                start = start.replace(a["wrong"], a["right"]) if a["kind"] == "global" \
                    else start.replace(a["wrong"], a["right"], 1)
        pos = nt.find(_norm(start))
        if pos < 0 and len(start) > 24:
            start = start[:24]
            pos = nt.find(_norm(start))
        if pos < 0 or pos <= last:
            lost += 1
            continue
        kept.append({**s, "start": start, "title": s.get("title", "")})
        last = pos
    if len(kept) < 3:
        return None, lost
    return {**plan, "segments": kept}, lost


def candidates_for(transcript: str, company: str, also: list[str] | None = None) -> dict:
    # 同音比對的名稱：本場公司＋同一天開法說會的公司（聯合法說會的另一家一定在其中）
    clean = lambda s: re.sub(r"[*＊]|-KY$", "", s).strip()     # noqa: E731
    pool = list(dict.fromkeys(clean(n) for n in [company] + list(also or []) if n))
    try:
        return homophone_candidates(transcript, pool)
    except Exception as e:  # noqa: BLE001  拼音套件缺或異常不影響校對
        log.warning("同音候選計算失敗：%s", e)
        return {}


def proofread(transcript: str, company: str, code: str, conf_date: str,
              who: str = "", also: list[str] | None = None,
              fixes: dict | None = None, model: str = "") -> tuple[str, dict]:
    """回 (校對後逐字稿, 報告 {applied, held, rejected, model})。額度用盡丟 RuntimeError。

    fixes：外部已產生的修正清單（例如 Claude 人工逐篇校對），直接套用、不呼叫 Gemini。
    """
    if len(transcript) < 300:
        return transcript, {"applied": [], "held": [], "rejected": []}
    names = company_names()
    if fixes is not None:
        new, applied, held, rejected = apply_fixes(transcript, fixes, names)
        log.info("校對 %s %s：套用 %d 筆（全文替換 %d 處）、待確認 %d、拒絕 %d｜%s", code, company,
                 len(applied), sum(a.get("n", 1) for a in applied), len(held), len(rejected), model)
        return new, {"applied": applied, "held": held, "rejected": rejected, "model": model}
    if exhausted_for(PROOF_MODELS):
        raise RuntimeError("Gemini 非 lite 模型今日額度已耗盡")
    cands = candidates_for(transcript, company, also)
    homo = ""
    if cands:
        homo = ("\n疑似公司名稱的同音辨識錯誤（出現次數，音同哪家公司）："
                + "、".join(f"{w}（{c} 次，音同{n}）" for w, (c, n) in list(cands.items())[:12])
                + "\n若上下文確實在講那家公司，請放進 global（wrong 直接用該詞本身）；"
                  "若是一般用語或講的不是那家公司，就不要改。\n")
    prompt = _PROMPT.format(date=conf_date, who=who or f"{company}（{code}）",
                            ctx=_ctx(company, code), homo=homo, all="、".join(names), text=transcript)
    resp = generate_with_retry(
        [prompt], timeout_ms=300_000, models=PROOF_MODELS,
        config_=types.GenerateContentConfig(temperature=0, system_instruction=_SYSTEM,
                                            response_mime_type="application/json"))
    try:
        fixes = json.loads(resp.text)
    except (ValueError, TypeError):
        log.warning("校對 %s %s：回傳不是 JSON，略過", code, company)
        return transcript, {"applied": [], "held": [], "rejected": []}
    new, applied, held, rejected = apply_fixes(transcript, fixes, names)
    model = getattr(resp, "model_version", "") or ""
    log.info("校對 %s %s：套用 %d 筆（全文替換 %d 處）、待確認 %d、拒絕 %d｜%s", code, company,
             len(applied), sum(a.get("n", 1) for a in applied), len(held), len(rejected), model)
    return new, {"applied": applied, "held": held, "rejected": rejected, "model": model,
                 "homophones": {w: c for w, (c, _n) in cands.items()}}
