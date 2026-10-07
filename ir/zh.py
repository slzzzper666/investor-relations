"""逐字稿繁體化：語音辨識（Whisper）常把國語吐成簡體字，存進 Notion 前一律轉成台灣繁體。

用 OpenCC 的 s2tw（只轉字形與台灣異體字），**不用 s2twp**：逐字稿是講者原話，
講者本來就用台灣用語，s2twp 會連詞彙一起改（「數據」→「資料」），等於竄改發言。
OpenCC 會把「台」轉成「臺」，站上與公司名稱慣用「台」（台積電、台灣），統一改回。

已是繁體的文字再轉一次結果不變（冪等），可以放心對任何文字重複套用。
"""
from functools import lru_cache


@lru_cache(maxsize=1)
def _cc():
    from opencc import OpenCC
    return OpenCC("s2tw")


def to_tw(text: str) -> str:
    if not text:
        return text
    return _cc().convert(text).replace("臺", "台")


def segments_to_tw(plan: dict | None) -> dict | None:
    """分段錨點檔一起轉：錨點 start 是拿來在逐字稿裡 find() 的原文片段，必須與逐字稿同一套字形。"""
    if not plan or not plan.get("segments"):
        return plan
    segs = [{**s, "title": to_tw(s.get("title", "")), "start": to_tw(s.get("start", ""))}
            for s in plan["segments"]]
    return {**plan, "segments": segs}
