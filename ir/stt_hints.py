"""語音辨識提示：語言＋繁體提示詞，全部從既有資料自動組出來，不花任何 AI 額度。

背景（2026-10 實測，矽格／台星科聯合法說會、台達電）：
- Whisper 沒指定語言時，中英夾雜的段落會被判成英文並「自行翻譯」，原本的中文內容消失
  （台達電一場出現 4 段不存在的英文句子）。證交所錄影檔名本身標了語言（_ch／_en），
  照檔名指定語言後，亂翻譯完全消失。
- 給一段繁體提示詞（公司名、族群、業務、術語），公司名稱錯字大幅下降：
  「台新科」14 次→0、「矽格」1 次→25 次（剩餘錯字交給 ir/proofread 文字校對）。
  提示詞是繁體、有標點，Whisper 輸出也跟著帶標點。

Whisper 的 prompt 上限約 224 token（中文約 1～2 token／字），所以控制在 PROMPT_MAX_CHARS 字內，
重要的放前面（公司名 > 業務 > 族群 > 術語）。
"""
import json
import re
from dataclasses import dataclass
from functools import lru_cache

import requests

import config
from ir.logger import get_logger

log = get_logger("ir.stt_hints")

SITE = "https://slzzzper666.github.io/investor-relations/"
PROMPT_MAX_CHARS = 110
GLOSSARY = ("營收、毛利率、營業利益率、稅後淨利、每股盈餘、資本支出、產能利用率、"
            "年增、季增、展望、AI伺服器、先進封裝、CoWoS、HPC")


@dataclass
class SttHints:
    language: str | None = None      # "zh"／"en"／None＝自動偵測
    prompt: str = ""


def language_of(video_url: str) -> str:
    """錄影檔名帶 _en → 英文場；其餘（_ch、YouTube、其他）一律中文。

    台股法說會絕大多數是中文；英文場證交所會另外上傳 _en 檔。
    自動偵測反而會把中英夾雜的中文場誤判成英文並翻譯，代價遠大於少數英文場被當中文。
    """
    return "en" if re.search(r"_en\.(mp4|mp3|m4a)", video_url or "", re.I) else "zh"


@lru_cache(maxsize=256)
def _company(code: str) -> dict:
    """公司頁資料（業務項目、族群）。本機有 data/business 就用本機，否則讀網站上的公司頁。"""
    local = config.DATA_DIR / "business" / f"{code}.json"
    if local.exists():
        try:
            return json.loads(local.read_text(encoding="utf-8"))
        except ValueError:
            pass
    try:
        r = requests.get(f"{SITE}company/{code}.json", timeout=15)
        if r.status_code == 200:
            return r.json().get("business") or {}
    except (requests.RequestException, ValueError):
        pass
    return {}


def build_prompt(company: str, code: str, conf_date: str, extra_companies: list[str] | None = None) -> str:
    # 提示詞只用「、」「。」：Whisper 會模仿提示詞的標點，放了全形括號（如「亞泥（1102）」
    # 「水泥（台灣）」）它就把括號當逗號用，輸出「台灣（大陸（兩塊（」。代號唸不出來，也不放。
    def plain(s: str) -> str:
        s = re.sub(r"[（）()\[\]【】]+", "", s)          # 水泥（台灣）→ 水泥台灣
        return re.sub(r"\s*[/／.]\s*", "、", s).strip("、 ")  # AI.ASIC.矽光子 → AI、ASIC、矽光子

    biz = _company(code) if code and code.isdigit() else {}
    names = [plain(re.sub(r"[*＊]$", "", company))] + [plain(x) for x in extra_companies or []]
    segs = [plain(s.get("name", "")) for s in biz.get("segments") or [] if s.get("name")]
    tags = [plain(t) for t in biz.get("tags") or []]
    year = (conf_date or "")[:4]
    head = "、".join(names) + (f"{year}年" if year else "") + "法說會。"
    body = "、".join(dict.fromkeys(segs + tags))
    text = head + (body + "。" if body else "") + GLOSSARY + "。"
    return text[:PROMPT_MAX_CHARS]


def hints_for(company: str, code: str, conf_date: str, video_url: str) -> SttHints:
    h = SttHints(language=language_of(video_url), prompt=build_prompt(company, code, conf_date))
    log.info("%s %s 語音辨識提示：語言 %s｜%s", code, company, h.language, h.prompt)
    return h
