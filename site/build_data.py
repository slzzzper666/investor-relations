"""第三階段：從 Notion 拉取全部法說會資料，輸出靜態網站用 JSON。

輸出（皆為 UTF-8、ensure_ascii=False）：
  site/public/list.json                清單（不含逐字稿，首頁用；每筆含 market_cap）
  site/public/detail/{code}_{date}.json 單筆完整資料（含逐字稿，詳細頁用）
  site/public/upcoming.json            MOPS 公告場次（2026-01 起～下月，行事曆用）

市值（億元）來源：TWSE / TPEx OpenAPI，當日快取於 site/.mcap_cache.json。

用法：
  .venv\\Scripts\\python site\\build_data.py
"""
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from notion_client import Client

BASE_DIR = Path(__file__).resolve().parent          # site/
ROOT_DIR = BASE_DIR.parent                          # 專案根目錄
PUBLIC_DIR = BASE_DIR / "public"
DETAIL_DIR = PUBLIC_DIR / "detail"
MCAP_CACHE = BASE_DIR / ".mcap_cache.json"

# 重用 ir 套件（MOPS 端點常數、ROC 日期解析、含 Proxy 偵測的 Session）
sys.path.insert(0, str(ROOT_DIR))
from ir.mops import AJAX_URL, _parse_date  # noqa: E402
from ir.net import get_session             # noqa: E402

TAIPEI = timezone(timedelta(hours=8))

load_dotenv(ROOT_DIR / ".env")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_PARENT_ID = os.getenv("NOTION_PARENT_ID", "")

TWSE_PRICE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TWSE_SHARES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_PRICE_URL = ("https://www.tpex.org.tw/openapi/v1/"
                  "tpex_mainboard_daily_close_quotes")
TPEX_SHARES_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"

ROC_DATE_RE = re.compile(r"\d{2,3}/\d{1,2}/\d{1,2}")


def _rich_text(prop: dict) -> str:
    """串接 rich_text 多段 2000 字的陣列。"""
    return "".join(t.get("plain_text", "")
                   for t in prop.get("rich_text", [])).strip()


def _title(prop: dict) -> str:
    return "".join(t.get("plain_text", "")
                   for t in prop.get("title", [])).strip()


def fetch_all_pages(client: Client, ds_id: str) -> list[dict]:
    """分頁撈出 data source 內全部頁面。"""
    pages: list[dict] = []
    cursor = None
    while True:
        kwargs: dict = {"data_source_id": ds_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.data_sources.query(**kwargs)
        pages.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return pages


def parse_page(page: dict) -> dict | None:
    p = page.get("properties", {})
    company = _title(p.get("公司", {}))
    if not company:
        return None

    code_num = p.get("股票代號", {}).get("number")
    code = str(int(code_num)) if code_num is not None else ""
    date = ((p.get("日期", {}).get("date") or {}).get("start") or "")[:10]

    return {
        "company": company,
        "code": code,
        "date": date,
        "pdf_url": p.get("簡報", {}).get("url") or "",
        "video_url": p.get("YT", {}).get("url") or "",
        "summary": _rich_text(p.get("重點摘要", {})),
        "ai_view": _rich_text(p.get("AI 觀點與未來方向分析", {})),
        "transcript": _rich_text(p.get("逐字稿", {})),
    }


def make_id(item: dict, used: set[str]) -> str:
    """detail 檔名：{code}_{date}；無代號時以公司名替代，重複再加流水號。"""
    head = item["code"] or re.sub(r"[^\w一-鿿-]", "", item["company"]) or "na"
    base = f"{head}_{item['date'] or 'nodate'}"
    candidate, n = base, 2
    while candidate in used:
        candidate = f"{base}-{n}"
        n += 1
    used.add(candidate)
    return candidate


# ---------- 未來場次（MOPS 當月＋下月） ----------

def _fetch_month_rows(market: str, year: int, month: int) -> list[dict]:
    """查 MOPS 單月法說會公告（同 ir.mops._fetch_month 的端點與參數）。

    自行解析列資料：_fetch_month 會略過日期為區間的列
    （如「115/07/08 至 115/07/10」，未來月份常見），此處取區間起始日。
    """
    s = get_session()
    r = s.post(AJAX_URL, data={
        "encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
        "TYPEK": market, "year": str(year - 1911), "month": f"{month:02d}",
    }, timeout=60)
    r.raise_for_status()
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "lxml")

    rows: list[dict] = []
    table = soup.find("table")
    if table is None:
        return rows
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 11:
            continue
        m = ROC_DATE_RE.search(tds[2].get_text(strip=True))
        conf_date = _parse_date(m.group(0)) if m else None
        if conf_date is None:
            continue
        rows.append({
            "code": tds[0].get_text(strip=True),
            "name": tds[1].get_text(strip=True),
            "date": conf_date.isoformat(),
            "time": tds[3].get_text(strip=True),
        })
    return rows


HISTORY_START = "2026-01-01"  # 行事曆顯示的 MOPS 公告起點


def fetch_upcoming() -> list[dict]:
    """抓 HISTORY_START 起到下個月（上市＋上櫃）的全部法說會公告。

    含過去未收錄的歷史場次（行事曆淡化顯示）與未來場次；
    已收錄場次由前端依 代號@日期 去重，這裡不過濾。
    """
    today = datetime.now(TAIPEI).date()
    if today.month == 12:
        last_ym = (today.year + 1, 1)
    else:
        last_ym = (today.year, today.month + 1)

    months: list[tuple[int, int]] = []
    y, m = int(HISTORY_START[:4]), int(HISTORY_START[5:7])
    while (y, m) <= last_ym:
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    seen: set[tuple[str, str]] = set()
    items: list[dict] = []
    for year, month in months:
        for market in ("sii", "otc"):
            try:
                rows = _fetch_month_rows(market, year, month)
            except Exception as exc:  # noqa: BLE001
                print(f"MOPS {market} {year}-{month:02d} 查詢失敗：{exc}")
                continue
            kept = 0
            for row in rows:
                key = (row["code"], row["date"])
                if row["date"] < HISTORY_START or key in seen:
                    continue
                seen.add(key)
                items.append(row)
                kept += 1
            print(f"MOPS {market} {year}-{month:02d}："
                  f"{len(rows)} 筆，收錄 {kept} 筆")
    items.sort(key=lambda x: (x["date"], x["code"]))
    return items


# ---------- 市值（億元） ----------

def _num(value) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return 0.0


def fetch_market_caps() -> dict[str, int]:
    """全市場市值表（code -> 億元整數），當日快取避免重複打 API。

    上市：TWSE OpenAPI 收盤價 × 已發行普通股數
    上櫃：TPEx OpenAPI 收盤價 × 發行股數
    """
    today = datetime.now(TAIPEI).strftime("%Y-%m-%d")
    if MCAP_CACHE.exists():
        try:
            cached = json.loads(MCAP_CACHE.read_text(encoding="utf-8"))
            if cached.get("date") == today and cached.get("caps"):
                print(f"市值：使用當日快取（{len(cached['caps'])} 檔）")
                return cached["caps"]
        except (ValueError, KeyError):
            pass

    s = get_session()

    def load(url: str, desc: str) -> list[dict]:
        try:
            r = s.get(url, timeout=60)
            r.raise_for_status()
            data = r.json()
            print(f"市值來源 {desc}：{len(data)} 筆")
            return data
        except Exception as exc:  # noqa: BLE001
            print(f"市值來源 {desc} 失敗：{exc}")
            return []

    prices: dict[str, float] = {}
    shares: dict[str, float] = {}

    for row in load(TWSE_PRICE_URL, "上市收盤價"):
        code = str(row.get("Code", "")).strip()
        price = _num(row.get("ClosingPrice"))
        if code and price > 0:
            prices[code] = price

    twse_rows = load(TWSE_SHARES_URL, "上市發行股數")
    share_key = next((k for k in (twse_rows[0] if twse_rows else {})
                      if "已發行普通股" in k), "")
    for row in twse_rows:
        code = str(row.get("公司代號", "")).strip()
        n = _num(row.get(share_key, 0)) if share_key else 0
        if code and n > 0:
            shares[code] = n

    for row in load(TPEX_PRICE_URL, "上櫃收盤價"):
        code = str(row.get("SecuritiesCompanyCode", "")).strip()
        price = _num(row.get("Close"))
        if re.fullmatch(r"\d{4}", code) and price > 0 and code not in prices:
            prices[code] = price

    for row in load(TPEX_SHARES_URL, "上櫃發行股數"):
        code = str(row.get("SecuritiesCompanyCode", "")).strip()
        n = _num(row.get("IssueShares"))
        if code and n > 0 and code not in shares:
            shares[code] = n

    caps = {code: int(round(prices[code] * shares[code] / 1e8))
            for code in prices.keys() & shares.keys()}
    print(f"市值表：{len(caps)} 檔")
    if caps:
        MCAP_CACHE.write_text(
            json.dumps({"date": today, "caps": caps}), encoding="utf-8")
    return caps


def main() -> None:
    if not NOTION_API_KEY or not NOTION_PARENT_ID:
        raise SystemExit("缺少 NOTION_API_KEY / NOTION_PARENT_ID（.env）")

    client = Client(auth=NOTION_API_KEY)
    db = client.databases.retrieve(database_id=NOTION_PARENT_ID)
    ds_id = db["data_sources"][0]["id"]
    print(f"data source：{ds_id}")

    pages = fetch_all_pages(client, ds_id)
    items = [it for pg in pages if (it := parse_page(pg))]
    # 日期新→舊，同日依代號排序
    items.sort(key=lambda x: (x["date"], x["code"]), reverse=True)

    upcoming = fetch_upcoming()
    caps = fetch_market_caps()

    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    if DETAIL_DIR.exists():
        shutil.rmtree(DETAIL_DIR)
    DETAIL_DIR.mkdir(parents=True)

    used_ids: set[str] = set()
    list_items: list[dict] = []
    for it in items:
        it_id = make_id(it, used_ids)
        detail = {"id": it_id, **it}
        (DETAIL_DIR / f"{it_id}.json").write_text(
            json.dumps(detail, ensure_ascii=False), encoding="utf-8")
        list_items.append({
            "id": it_id,
            "company": it["company"],
            "code": it["code"],
            "date": it["date"],
            "market_cap": caps.get(it["code"], 0),
            "pdf_url": it["pdf_url"],
            "video_url": it["video_url"],
            "summary": it["summary"],
            "ai_view": it["ai_view"],
            "has_transcript": bool(it["transcript"]),
            "transcript_chars": len(it["transcript"]),
        })

    generated_at = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
    payload = {
        "generated_at": generated_at,
        "count": len(list_items),
        "items": list_items,
    }
    list_path = PUBLIC_DIR / "list.json"
    list_path.write_text(json.dumps(payload, ensure_ascii=False),
                         encoding="utf-8")

    for u in upcoming:
        u["market_cap"] = caps.get(u["code"], 0)
    upcoming_payload = {
        "generated_at": generated_at,
        "count": len(upcoming),
        "items": upcoming,
    }
    upcoming_path = PUBLIC_DIR / "upcoming.json"
    upcoming_path.write_text(json.dumps(upcoming_payload, ensure_ascii=False),
                             encoding="utf-8")

    print(f"共 {len(list_items)} 筆")
    for li in list_items:
        print(f"  {li['date']}  {li['code'] or '----':>4}  {li['company']}"
              f"  市值 {li['market_cap']:,} 億"
              f"  逐字稿 {li['transcript_chars']:,} 字")
    no_cap = sum(1 for li in list_items if not li["market_cap"]) \
        + sum(1 for u in upcoming if not u["market_cap"])
    print(f"list.json：{list_path}（{list_path.stat().st_size:,} bytes）")
    print(f"detail/：{len(list_items)} 個檔案")
    print(f"upcoming.json：{len(upcoming)} 筆 MOPS 公告場次"
          f"（{upcoming_path.stat().st_size:,} bytes）")
    if no_cap:
        print(f"註：{no_cap} 筆查無市值（market_cap=0，排序置後）")


if __name__ == "__main__":
    main()
