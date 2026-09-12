"""每月健康回報：把站台與管線的關鍵狀態推到 TG/DC。

背景：2026 年 7～9 月連續發生「系統壞了沒人知道」——Actions 因 60 天無 commit 被停用
兩週、Railway 成功率掉到 7/32、美股建置靜默失敗兩個月、逐字稿斷三個月，全靠人剛好
去看才發現。這支每月跑一次，至少讓問題最多藏一個月。

檢查項目（皆從站上已產出的 JSON 讀，不重抓外部資料）：
  - 台股：本月新增場次、逐字稿覆蓋率、站上最新日期（Notion 最新場次距今幾天＝Railway 心跳）
  - 美股：本月新增財報季、最新公布日
  - 總經：經濟數據筆數、歷史數據最新日、每月回顧最新月份
  - CI：最近 10 次部署成功率（GITHUB_TOKEN，僅在 Actions 內可用）
異常條件命中就在訊息開頭加 ⚠️。

用法：python site/monthly_report.py            推播
      python site/monthly_report.py --dry-run  只印出
"""
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
PUBLIC_DIR = BASE_DIR / "public"
sys.path.insert(0, str(ROOT_DIR))

from config import TZ_TAIPEI                       # noqa: E402
from ir.notify import push_text                    # noqa: E402


def _load(name: str) -> dict:
    p = PUBLIC_DIR / name
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _ci_status() -> tuple[int, int] | None:
    """最近 10 次「部署法說會觀測站」的 (成功, 總數)；沒有 token 回 None。"""
    token, repo = os.getenv("GITHUB_TOKEN", ""), os.getenv("GITHUB_REPOSITORY", "")
    if not token or not repo:
        return None
    try:
        r = requests.get(
            f"https://api.github.com/repos/{repo}/actions/workflows/pages.yml/runs",
            params={"per_page": 10}, timeout=20,
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"})
        r.raise_for_status()
        runs = r.json().get("workflow_runs", [])
        return sum(1 for x in runs if x.get("conclusion") == "success"), len(runs)
    except Exception:  # noqa: BLE001
        return None


def build_report() -> tuple[str, list[str]]:
    today = datetime.now(TZ_TAIPEI).date()
    ym = today.strftime("%Y-%m")
    last_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    warnings: list[str] = []

    tw = _load("list.json")
    items = tw.get("items", [])
    tw_month = [x for x in items if x.get("date", "").startswith(ym)]
    tw_last = [x for x in items if x.get("date", "").startswith(last_month)]
    tw_tr = sum(1 for x in tw_last if x.get("transcript_chars"))
    latest_tw = max((x.get("date", "") for x in items), default="")
    gap = (today - date.fromisoformat(latest_tw)).days if latest_tw else 999
    if gap > 4:
        warnings.append(f"台股最新場次已是 {gap} 天前（{latest_tw}），Railway 每日管線可能停擺")
    if tw_last and tw_tr / len(tw_last) < 0.15:
        warnings.append(f"上月逐字稿覆蓋率僅 {tw_tr / len(tw_last) * 100:.0f}%，錄影抓取可能失效")

    us = _load("us_list.json")
    us_items = us.get("items", [])
    us_last = [x for x in us_items if x.get("date", "").startswith(last_month)]
    latest_us = max((x.get("date", "") for x in us_items), default="")

    mf = _load("macro_future.json")
    econ_n = len(mf.get("econ", []))
    if econ_n == 0:
        warnings.append("總經經濟數據 0 筆（FRED 抓取失效）")
    mh = _load("macro_history.json")
    hist_latest = max((x.get("date", "") for x in mh.get("items", [])), default="")
    if hist_latest and (today - date.fromisoformat(hist_latest)).days > 45:
        warnings.append(f"總經歷史數據停在 {hist_latest}")
    mm = _load("macro_monthly.json")
    months = [m.get("month", "") for m in mm.get("months", [])]
    if last_month not in months:
        warnings.append(f"每月回顧缺 {last_month}")

    ci = _ci_status()
    if ci and ci[1] and ci[0] / ci[1] < 0.7:
        warnings.append(f"CI 最近 {ci[1]} 次部署僅成功 {ci[0]} 次")

    lines = [
        f"📋 法說會觀測站 月報｜{today:%Y/%m/%d}",
        "━━━━━━━━━━━━━━━━━━",
        f"🇹🇼 台股：{last_month} 共 {len(tw_last)} 場、逐字稿 {tw_tr} 場"
        f"（{tw_tr / len(tw_last) * 100:.0f}%）" if tw_last else f"🇹🇼 台股：{last_month} 無場次",
        f"　　本月至今 {len(tw_month)} 場｜站上共 {len(items):,} 場｜最新 {latest_tw}",
        f"🇺🇸 美股：{last_month} 新增 {len(us_last)} 季｜共 {len(us_items)} 季｜最新 {latest_us}",
        f"📊 總經：經濟數據 {econ_n} 筆｜歷史至 {hist_latest}｜回顧至 {months[-1] if months else '—'}",
    ]
    if ci:
        lines.append(f"⚙️ CI：最近 {ci[1]} 次部署成功 {ci[0]} 次")
    lines.append("━━━━━━━━━━━━━━━━━━")
    if warnings:
        lines.insert(0, "⚠️ 異常：" + "；".join(warnings))
    else:
        lines.append("✅ 各項正常")
    return "\n".join(lines), warnings


def main() -> None:
    text, warnings = build_report()
    print(text)
    if "--dry-run" in sys.argv:
        return
    push_text(text)


if __name__ == "__main__":
    main()
