"""稽核：逐字稿來自 YouTube 搜尋（MOPS 沒登載影音）的場次，當初抓到的是不是評論頻道。

背景（2026-10）：ir/media 的 YouTube 搜尋只看標題，抓進了「價值股雷達」「Wa People」
等評論頻道的影片，當成法說會轉成逐字稿（穩懋、奇鋐各 5 場）。media 已改成只收
公司／交易所／券商頻道。當初用的影片網址沒存進 Notion，所以這裡用「舊規則」重搜一次：
舊規則會選中、但上傳頻道不是官方的 → 列為可疑。

輸出 data/proofread/yt_audit.json：[{id, company, title, channel}]
用法：python audit_yt_sources.py
"""
import json
import re
import time

import yt_dlp

import config
from ir.logger import get_logger
from ir.media import _OFFICIAL_CHANNELS

log = get_logger("ir.yt_audit")
PUBLIC = config.BASE_DIR / "site" / "public"
OUT = config.DATA_DIR / "proofread" / "yt_audit.json"
KEYWORDS = ("法說會", "法人說明會", "業績發表", "investor conference")


def main() -> None:
    items = json.loads((PUBLIC / "list.json").read_text(encoding="utf-8"))["items"]
    todo = [x for x in items if x.get("transcript_chars", 0) >= 300 and not x.get("has_video")]
    log.info("MOPS 沒登載影音的逐字稿：%d 篇", len(todo))
    cache: dict[str, list] = {}
    bad = []
    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True, "noprogress": True}) as ydl:
        for x in todo:
            name = re.sub(r"[*＊]$", "", x["company"])
            q = f"ytsearch5:{name} 法說會 {x['date'][:4]}"
            if q not in cache:
                try:
                    cache[q] = (ydl.extract_info(q, download=False) or {}).get("entries") or []
                except Exception as e:  # noqa: BLE001
                    log.warning("搜尋失敗 %s：%s", q, e)
                    cache[q] = []
                time.sleep(1)
            short = re.sub(r"-KY$", "", name)
            for e in cache[q]:
                title, ch = e.get("title", ""), e.get("channel") or e.get("uploader") or ""
                if name not in title or not any(k in title.lower() for k in KEYWORDS):
                    continue
                if (e.get("duration") or 0) and e["duration"] < 600:
                    continue
                # 舊規則第一個選中的就是這支
                if not (short in ch or any(k in ch for k in _OFFICIAL_CHANNELS)):
                    bad.append({"id": x["id"], "company": name, "title": title, "channel": ch})
                    log.info("XX %s｜%s｜%s", x["id"], ch, title[:50])
                break
    OUT.write_text(json.dumps(bad, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("===== 可疑（舊規則會選到非官方頻道）%d 篇，共查 %d 篇 =====", len(bad), len(todo))


if __name__ == "__main__":
    main()
