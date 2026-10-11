"""把線上網站的 list.json 與台股 detail/*.json 拉回本機 site/public（只用來對齊最新逐字稿）。

proofread_backfill 讀的是本機 site/public/detail；本機好幾天沒 build 時，那份是舊稿，
拿舊稿套修正寫回 Notion 會蓋掉之後的改動（例如幻聽清除）。批次校對前先跑這支。
用法：python pull_live_site.py
"""
import json
from concurrent.futures import ThreadPoolExecutor

import requests

import config

SITE = "https://slzzzper666.github.io/investor-relations/"
PUBLIC = config.BASE_DIR / "site" / "public"


def main() -> None:
    lst = requests.get(SITE + "list.json", timeout=60)
    lst.raise_for_status()
    (PUBLIC / "list.json").write_bytes(lst.content)
    ids = [x["id"] for x in lst.json()["items"] if x.get("transcript_chars", 0) >= 300]
    (PUBLIC / "detail").mkdir(parents=True, exist_ok=True)

    def get(i: str) -> bool:
        r = requests.get(f"{SITE}detail/{i}.json", timeout=60)
        if r.status_code != 200:
            return False
        json.loads(r.content)                       # 確認是完整 JSON
        (PUBLIC / "detail" / f"{i}.json").write_bytes(r.content)
        return True

    with ThreadPoolExecutor(8) as ex:
        ok = sum(ex.map(get, ids))
    print(f"list.json＋detail {ok}/{len(ids)} 篇已更新")


if __name__ == "__main__":
    main()
