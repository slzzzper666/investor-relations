"""第三階段：從 Notion 拉取全部法說會資料，輸出靜態網站用 JSON。

輸出（皆為 UTF-8、ensure_ascii=False）：
  site/public/list.json                清單（不含逐字稿，首頁用；每筆含 market_cap）
  site/public/detail/{code}_{date}.json 單筆完整資料（含逐字稿，詳細頁用）
  site/public/upcoming.json            MOPS 公告場次（2026-01 起～下月，行事曆用）
  site/public/c/{id}.html              每場法說會的純靜態頁（SEO 用，完整內容在 HTML）
  site/public/sitemap.xml、robots.txt   搜尋引擎收錄

市值（億元）來源：TWSE / TPEx OpenAPI，當日快取於 site/.mcap_cache.json。

用法：
  .venv\\Scripts\\python site\\build_data.py
"""
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import urllib3
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from notion_client import Client

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = Path(__file__).resolve().parent          # site/
ROOT_DIR = BASE_DIR.parent                          # 專案根目錄
PUBLIC_DIR = BASE_DIR / "public"
DETAIL_DIR = PUBLIC_DIR / "detail"
SEGMENTS_DIR = ROOT_DIR / "data" / "segments"   # 逐字稿分段（錨點），由 Claude/AI 產出
MCAP_CACHE = BASE_DIR / ".mcap_cache.json"
FIN_CACHE = BASE_DIR / ".fin_cache.json"
PE_CACHE = BASE_DIR / ".pe_cache.json"

# 重用 ir 套件（MOPS 端點常數、ROC 日期解析、含 Proxy 偵測的 Session）
sys.path.insert(0, str(ROOT_DIR))
from ir.mops import AJAX_URL, _parse_date  # noqa: E402
from ir.net import get_session             # noqa: E402
from ir.radar.tw import _ccsi, _pct_change, _single_quarter  # noqa: E402

TAIPEI = timezone(timedelta(hours=8))

load_dotenv(ROOT_DIR / ".env")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_PARENT_ID = os.getenv("NOTION_PARENT_ID", "")

TWSE_PRICE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TWSE_SHARES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_PRICE_URL = ("https://www.tpex.org.tw/openapi/v1/"
                  "tpex_mainboard_daily_close_quotes")
TPEX_SHARES_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TWSE_PE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
TPEX_PE_URL = ("https://www.tpex.org.tw/openapi/v1/"
               "tpex_mainboard_peratio_analysis")

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


# ---------- SEO：靜態頁、sitemap、robots ----------

SITE_BASE = "https://slzzzper666.github.io/investor-relations"
STATIC_DIR = PUBLIC_DIR / "c"

# robots.txt：歡迎帶流量的搜尋引擎，封鎖 AI 訓練與大量採集機器人
SEARCH_BOTS = ("Googlebot", "Bingbot", "DuckDuckBot", "Applebot")
BLOCKED_BOTS = (
    "GPTBot", "OAI-SearchBot", "ChatGPT-User", "Google-Extended", "CCBot",
    "ClaudeBot", "anthropic-ai", "PerplexityBot", "Bytespider", "Amazonbot",
    "Applebot-Extended", "meta-externalagent", "FacebookBot", "Diffbot",
    "Omgilibot", "DataForSeoBot", "ImagesiftBot",
    "AhrefsBot", "SemrushBot", "MJ12bot", "DotBot", "PetalBot",
)
COPYRIGHT = ("© 2026 法說會觀測站　本站彙整內容禁止未經授權之大量擷取、"
             "轉載或商業利用，違者將依法處理。")


def _esc(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _summary_html(summary: str) -> str:
    """重點摘要欄位：首行是一句話總結，其後是「• 」開頭的條列。"""
    lines = [ln.strip() for ln in summary.split("\n") if ln.strip()]
    if not lines:
        return ""
    html = f"<p class='lede'>{_esc(lines[0])}</p>"
    bullets = [ln.lstrip('• ').strip() for ln in lines[1:] if ln.startswith('•')]
    if bullets:
        html += "<ul>" + "".join(f"<li>{_esc(b)}</li>" for b in bullets) + "</ul>"
    return html


def _transcript_html(transcript: str) -> str:
    paras = [ln.strip() for ln in transcript.split("\n") if ln.strip()]
    return "".join(f"<p>{_esc(p)}</p>" for p in paras)


def _pct_str(v) -> str:
    """年增/季增百分比 → '（年增 +12.3%）'樣式用的帶號字串；無值回空。"""
    if v is None:
        return ""
    return f"{'+' if v >= 0 else ''}{v:.1f}%"


def _fin_static_html(fin: dict | None) -> str:
    """純靜態頁的財報數據區塊（文字版，供 SEO 收錄）。"""
    if not fin:
        return ""
    rows = []
    if fin.get("market_cap"):
        rows.append(f"<li>市值：約 {fin['market_cap']:,} 億元</li>")
    if fin.get("revenue") is not None:
        sub = [s for s in (
            f"年增 {_pct_str(fin.get('revenue_yoy'))}" if fin.get("revenue_yoy") is not None else "",
            f"季增 {_pct_str(fin.get('revenue_qoq'))}" if fin.get("revenue_qoq") is not None else "",
        ) if s]
        tail = f"（{' ／ '.join(sub)}）" if sub else ""
        rows.append(f"<li>單季營收：{fin['revenue']:,.2f} 億元{tail}</li>")
    if fin.get("eps") is not None:
        tail = (f"（年增 {_pct_str(fin.get('eps_yoy'))}）"
                if fin.get("eps_yoy") is not None else "")
        rows.append(f"<li>單季 EPS：{fin['eps']:.2f} 元{tail}</li>")
    if fin.get("gross_margin") is not None:
        rows.append(f"<li>毛利率：{fin['gross_margin']:.1f}%</li>")
    if fin.get("pe") is not None:
        rows.append(f"<li>本益比：{fin['pe']:.1f} 倍</li>")
    if fin.get("capex") is not None:
        rows.append(f"<li>單季資本支出：{fin['capex']:,.2f} 億元</li>")
    if not rows:
        return ""
    return (f"<h2>最新財報數據（{_esc(fin.get('period', ''))}）</h2>"
            f"<ul>{''.join(rows)}</ul>")


def render_static_page(d: dict) -> str:
    """單場法說會的純靜態 HTML（內容直接在 DOM，供搜尋引擎完整收錄）。"""
    title = f"{d['company']}（{d['code']}）法說會逐字稿與 AI 分析｜{d['date']}"
    one_liner = d["summary"].split("\n")[0].strip() if d["summary"] else ""
    desc = _esc((one_liner or f"{d['company']} {d['date']} 法人說明會重點整理")[:150])
    url = f"{SITE_BASE}/c/{d['id']}.html"

    links = []
    if d["pdf_url"]:
        links.append(f"<a href='{_esc(d['pdf_url'])}' rel='nofollow'>簡報 PDF</a>")
    if d["video_url"]:
        links.append(f"<a href='{_esc(d['video_url'])}' rel='nofollow'>法說會影音</a>")
    links.append(f"<a href='../detail.html?id={d['id']}'>互動介面開啟</a>")

    ai_block = ""
    if d["ai_view"]:
        ai_block = ("<h2>AI 觀點與未來方向</h2>"
                    + "".join(f"<p>{_esc(p.strip())}</p>"
                              for p in d["ai_view"].split("\n") if p.strip()))

    transcript_block = ""
    if d["transcript"]:
        transcript_block = (
            "<h2>完整逐字稿</h2><details><summary>展開逐字稿"
            f"（{len(d['transcript']):,} 字）</summary>"
            f"{_transcript_html(d['transcript'])}</details>")

    jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "datePublished": d["date"],
        "inLanguage": "zh-Hant-TW",
        "author": {"@type": "Organization", "name": "法說會觀測站"},
        "publisher": {"@type": "Organization", "name": "法說會觀測站"},
        "mainEntityOfPage": url,
    }, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}｜法說會觀測站</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="article">
<meta property="og:title" content="{_esc(title)}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{url}">
<meta property="og:site_name" content="法說會觀測站">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+TC:wght@400;500;700&family=Noto+Serif+TC:wght@600;700&display=swap" rel="stylesheet">
<script src="../assets/analytics.js"></script>
<style>
  :root {{ --bg:#0B0E13; --ink:#E9E4D8; --dim:#9A937F; --amber:#D9A441; }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font:16px/1.85 "Noto Sans TC",sans-serif; }}
  .page {{ max-width:760px; margin:0 auto; padding:32px 20px 64px; }}
  .site {{ font-family:"IBM Plex Mono",monospace; font-size:13px;
           color:var(--dim); text-decoration:none; letter-spacing:.08em; }}
  .site:hover {{ color:var(--amber) }}
  h1 {{ font-family:"Noto Serif TC",serif; font-size:28px; line-height:1.4;
        margin:18px 0 4px; }}
  h2 {{ font-family:"Noto Serif TC",serif; font-size:20px; margin:36px 0 12px;
        padding-top:18px; border-top:1px solid #232838; }}
  .meta {{ font-family:"IBM Plex Mono",monospace; font-size:13px;
           color:var(--dim); }}
  .lede {{ font-size:18px; border-left:3px solid var(--amber);
           padding-left:14px; }}
  ul {{ padding-left:22px }} li {{ margin:6px 0 }}
  a {{ color:var(--amber) }}
  .links a {{ margin-right:18px }}
  details summary {{ cursor:pointer; color:var(--amber); margin-bottom:12px }}
  footer {{ margin-top:48px; font-size:13px; color:var(--dim);
            border-top:1px solid #232838; padding-top:16px; }}
</style>
<script type="application/ld+json">{jsonld}</script>
</head>
<body>
<div class="page">
<a class="site" href="../">← 法說會觀測站</a>
<h1>{_esc(d['company'])}（{_esc(d['code'])}）法人說明會</h1>
<p class="meta">{d['date']}</p>
<h2>重點摘要</h2>
{_summary_html(d['summary'])}
{_fin_static_html(d.get('financials'))}
{ai_block}
<p class="links">{'　'.join(links)}</p>
{transcript_block}
<footer>逐字稿由語音辨識產生、摘要與觀點由 AI 彙整，內容僅供研究參考，不構成投資建議。資料來源：公開資訊觀測站（MOPS）與各公司公開影音。<br>{COPYRIGHT}</footer>
</div>
</body>
</html>"""


def write_seo_files(list_items: list[dict], details: list[dict]) -> None:
    if STATIC_DIR.exists():
        shutil.rmtree(STATIC_DIR)
    STATIC_DIR.mkdir(parents=True)
    for d in details:
        (STATIC_DIR / f"{d['id']}.html").write_text(
            render_static_page(d), encoding="utf-8")

    urls = [f"{SITE_BASE}/"]
    urls += [f"{SITE_BASE}/c/{it['id']}.html" for it in list_items]
    entries = "\n".join(
        f"  <url><loc>{u}</loc></url>" for u in urls)
    (PUBLIC_DIR / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</urlset>\n", encoding="utf-8")

    lines = ["# 搜尋引擎歡迎收錄（帶來自然流量）"]
    for b in SEARCH_BOTS:
        lines += [f"User-agent: {b}", "Allow: /", ""]
    lines += ["# 封鎖 AI 訓練爬蟲與大量採集 / SEO 掃描機器人"]
    for b in BLOCKED_BOTS:
        lines += [f"User-agent: {b}", "Disallow: /", ""]
    lines += ["# 其餘維持一般可見", "User-agent: *", "Allow: /", "",
              f"Sitemap: {SITE_BASE}/sitemap.xml", ""]
    (PUBLIC_DIR / "robots.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"SEO：{len(details)} 個靜態頁、sitemap {len(urls)} 條、"
          f"robots.txt（封鎖 {len(BLOCKED_BOTS)} 種爬蟲）")


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


# ---------- 本益比（隨股價每日變動，當日快取） ----------

def fetch_pe_ratios() -> dict[str, float]:
    """全市場本益比表（code -> PE），當日快取。

    上市：TWSE BWIBBU_ALL；上櫃：TPEx 個股本益比分析。
    PE ≤ 0 或無值（虧損 / 無資料）一律略過。
    """
    today = datetime.now(TAIPEI).strftime("%Y-%m-%d")
    if PE_CACHE.exists():
        try:
            cached = json.loads(PE_CACHE.read_text(encoding="utf-8"))
            if cached.get("date") == today and cached.get("pe"):
                print(f"本益比：使用當日快取（{len(cached['pe'])} 檔）")
                return cached["pe"]
        except (ValueError, KeyError):
            pass

    s = get_session()

    def grab(url: str, code_key: str, pe_key: str, desc: str) -> dict:
        try:
            try:
                data = s.get(url, timeout=60).json()
            except requests.exceptions.SSLError:
                data = s.get(url, timeout=60, verify=False).json()  # TPEx 憑證偶發
        except Exception as exc:  # noqa: BLE001
            print(f"本益比來源 {desc} 失敗：{exc}")
            return {}
        out = {}
        for row in data:
            code = str(row.get(code_key, "")).strip()
            pe = _num(row.get(pe_key))
            if re.fullmatch(r"\d{4}", code) and pe > 0:
                out[code] = round(pe, 2)
        print(f"本益比來源 {desc}：{len(out)} 檔")
        return out

    pes: dict[str, float] = {}
    pes.update(grab(TWSE_PE_URL, "Code", "PEratio", "上市"))
    pes.update(grab(TPEX_PE_URL, "SecuritiesCompanyCode",
                    "PriceEarningRatio", "上櫃"))
    print(f"本益比表：{len(pes)} 檔")
    if pes:
        PE_CACHE.write_text(json.dumps({"date": today, "pe": pes}),
                            encoding="utf-8")
    return pes


# ---------- 個股財報快照（詳細頁右欄） ----------

def _load_fin_cache() -> dict:
    if FIN_CACHE.exists():
        try:
            return json.loads(FIN_CACHE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def _target_quarter(today) -> tuple[int, int]:
    """依台股法定財報截止日，推算目前最可能已公布的最新一季 (ROC 年, 季)。"""
    roc = today.year - 1911
    md = (today.month, today.day)
    if md >= (11, 14):
        return roc, 3
    if md >= (8, 14):
        return roc, 2
    if md >= (5, 15):
        return roc, 1
    if md >= (3, 31):
        return roc - 1, 4
    return roc - 1, 3


def _parse_capex_num(s: str) -> float | None:
    """'-11,091,192' 或 '(11,091,192)' → float（仟元，含正負號）。"""
    t = str(s).strip().replace(",", "")
    if not t or t in ("-", "--"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


CAPEX_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t164sb05"


def _capex_cumulative(code: str, roc_year: int, season: int) -> float | None:
    """舊版 MOPS 現金流量表 → 累計『取得不動產、廠房及設備』（仟元，含號）。"""
    try:
        r = get_session().post(CAPEX_URL, data={
            "encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
            "queryName": "co_id", "inpuType": "co_id", "TYPEK": "all",
            "isnew": "false", "co_id": code, "year": str(roc_year),
            "season": f"{season:02d}",
        }, timeout=30)
        r.raise_for_status()
    except requests.RequestException:
        return None
    finally:
        time.sleep(1)
    soup = BeautifulSoup(r.text, "lxml")
    for tr in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cells:
            continue
        label = cells[0]
        if "不動產" in label and "設備" in label and (
                "取得" in label or "購置" in label):
            for c in cells[1:]:
                v = _parse_capex_num(c)
                if v is not None:
                    return v
    return None


def fetch_tw_financials(code: str, cache: dict) -> dict | None:
    """單檔台股最新一季財報快照（季靜態：營收/EPS/毛利率/CapEx + YoY、QoQ）。

    取材自 MOPS t163sb01（損益表）與舊版 t164sb05（現金流量表）；
    皆採累計相減算單季。依目標季快取於 .fin_cache.json，同季不重打。
    市值、本益比為股價型（每日變動），不在此處，於組裝階段注入。
    回 None＝非四碼代號或該季資料尚未公布。
    """
    if not re.fullmatch(r"\d{4}", code or ""):
        return None

    today = datetime.now(TAIPEI).date()
    ty, ts = _target_quarter(today)
    cached = cache.get(code)
    if cached and cached.get("v") == 2 and cached.get("period_key") == [ty, ts]:
        return cached.get("data")

    # 候選：目標季 → 再退一季（部分公司公布較晚）
    older = (ty, ts - 1) if ts > 1 else (ty - 1, 4)
    for y, s in (ty, ts), older:
        try:
            cur = _ccsi(code, y, s)
            if not cur:
                continue
            rev, eps = _single_quarter(code, y, s)
            rev_ly, eps_ly = _single_quarter(code, y - 1, s)
            rev_pq = (_single_quarter(code, y - 1, 4)
                      if s == 1 else _single_quarter(code, y, s - 1))[0]
        except Exception as exc:  # noqa: BLE001
            print(f"財報 {code} {y}Q{s} 抓取失敗：{exc}")
            continue
        if eps is None and rev is None:
            continue

        # 毛利率（單季）：單季毛利 / 單季營收。金控/證券無營業毛利 → None
        gross_margin = None
        g_cur = cur.get("gross")
        if g_cur is not None and rev not in (None, 0):
            g_single = (g_cur if s == 1
                        else (g_cur - _ccsi(code, y, s - 1).get("gross", 0)
                              if _ccsi(code, y, s - 1).get("gross") is not None
                              else None))
            if g_single is not None:
                gross_margin = round(g_single / rev * 100, 1)

        # CapEx（單季，億元，取絕對值＝當季資本支出規模）
        capex = None
        try:
            c_cur = _capex_cumulative(code, y, s)
            if c_cur is not None:
                if s == 1:
                    c_single = c_cur
                else:
                    c_prev = _capex_cumulative(code, y, s - 1)
                    c_single = c_cur - c_prev if c_prev is not None else None
                if c_single is not None:
                    capex = round(abs(c_single) / 1e5, 2)  # 仟元→億元
        except Exception as exc:  # noqa: BLE001
            print(f"CapEx {code} {y}Q{s} 抓取失敗：{exc}")

        data = {
            "period": f"{y + 1911} Q{s}",
            "eps": eps,
            "eps_yoy": _pct_change(eps, eps_ly),
            "eps_last_year": eps_ly,
            "revenue": round(rev / 1e5, 2) if rev is not None else None,  # 仟元→億元
            "revenue_yoy": _pct_change(rev, rev_ly),
            "revenue_qoq": _pct_change(rev, rev_pq),
            "gross_margin": gross_margin,
            "capex": capex,
        }
        cache[code] = {"v": 2, "period_key": [ty, ts], "data": data}
        return data
    return None


def _load_segments(it_id: str, transcript: str):
    """讀 data/segments/{id}.json 的「錨點」分段，依錨點切原始逐字稿成結構化段落。

    錨點檔每段 {type: intro|topic|qa, title, start}；start 是逐字稿中該段開頭的
    一小段原文，用來定位切點。回 [{type, title, text}] 或 None（無檔/對不上）。
    """
    if not transcript:
        return None
    seg_file = SEGMENTS_DIR / f"{it_id}.json"
    if not seg_file.exists():
        return None
    try:
        anchors = json.loads(seg_file.read_text(encoding="utf-8")).get("segments", [])
    except (ValueError, OSError):
        return None
    # 台/臺 變體統一後比對（替換長度不變，位置不偏移 → 仍可用原文切片）
    ntrans = transcript.replace("臺", "台")
    found = []
    for a in anchors:
        start = a.get("start", "")
        pos = ntrans.find(start.replace("臺", "台")) if start else -1
        if pos >= 0:
            found.append((pos, a))
    if not found:
        return None
    found.sort(key=lambda x: x[0])
    out = []
    intro = transcript[:found[0][0]].strip()
    if intro:
        out.append({"type": "intro", "title": "", "text": intro})
    for i, (pos, a) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else len(transcript)
        out.append({"type": a.get("type", "topic"),
                    "title": a.get("title", ""),
                    "text": transcript[pos:end].strip()})
    return out


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
    pes = fetch_pe_ratios()
    fin_cache = _load_fin_cache()
    # 財報逐檔抓 MOPS（t163sb01/t164sb05）。雲端(GitHub Actions)上又慢又不可靠：
    # 未快取的股票各要打多支 MOPS（部分回 406）＋ _capex 內每檔 sleep，數百檔把 build
    # 拖到 40+ 分。故雲端設環境變數 IR_SKIP_FINANCIALS=1 → 財報全用既有 .fin_cache.json、
    # 完全不連 MOPS（約 5 分完成）；本機不設此旗標、照常抓財報並更新快取。
    # 另留斷路器：萬一忘了設旗標、又連續 20 檔抓不到，自動止血改用快取。
    mops_ok = not os.getenv("IR_SKIP_FINANCIALS")
    consec_none = 0
    FIN_FAIL_LIMIT = 20
    if not mops_ok:
        print("IR_SKIP_FINANCIALS 已設：財報全用既有快取、不連 MOPS（雲端部署加速）")

    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    DETAIL_DIR.mkdir(parents=True, exist_ok=True)
    for f in DETAIL_DIR.glob("*.json"):  # 只清台股，保留美股 us-* 由 build_us.py 管
        if not f.name.startswith("us-"):
            f.unlink()

    used_ids: set[str] = set()
    list_items: list[dict] = []
    details: list[dict] = []
    for it in items:
        it_id = make_id(it, used_ids)
        detail = {"id": it_id, **it}
        if mops_ok:
            fin = fetch_tw_financials(it["code"], fin_cache)
            if fin is None:
                consec_none += 1
                if consec_none >= FIN_FAIL_LIMIT:
                    mops_ok = False
                    print(f"連續 {FIN_FAIL_LIMIT} 檔財報無結果（疑似 MOPS 擋/限流），"
                          f"其餘改用既有快取、不再逐檔連線")
            else:
                consec_none = 0
        else:
            cached = fin_cache.get(it["code"])
            fin = cached.get("data") if cached else None
        if fin is not None:
            fin = {**fin,  # 股價型數據每日變動，組裝時注入
                   "market_cap": caps.get(it["code"]) or None,
                   "pe": pes.get(it["code"])}
        detail["financials"] = fin
        segs = _load_segments(it_id, it.get("transcript", ""))
        if segs:
            detail["transcript_segments"] = segs
        details.append(detail)
        (DETAIL_DIR / f"{it_id}.json").write_text(
            json.dumps(detail, ensure_ascii=False), encoding="utf-8")
        # list.json 只供「列表/行事曆」用：僅留一句話總結，丟掉完整摘要與 ai_view
        # （詳細頁另抓 detail/{id}.json 取完整內容）。可把首頁載入由數 MB 降到數百 KB。
        one_liner = it["summary"].split("\n")[0].strip() if it["summary"] else ""
        list_items.append({
            "id": it_id,
            "company": it["company"],
            "code": it["code"],
            "date": it["date"],
            "market_cap": caps.get(it["code"], 0),
            "pdf_url": it["pdf_url"],
            "video_url": it["video_url"],
            "summary": one_liner,
            "has_transcript": bool(it["transcript"]),
            "transcript_chars": len(it["transcript"]),
        })

    FIN_CACHE.write_text(json.dumps(fin_cache, ensure_ascii=False),
                         encoding="utf-8")
    fin_n = sum(1 for d in details if d.get("financials"))
    print(f"財報快照：{fin_n}/{len(details)} 筆有最新一季數據")

    generated_at = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
    payload = {
        "generated_at": generated_at,
        "count": len(list_items),
        "items": list_items,
    }
    list_path = PUBLIC_DIR / "list.json"
    list_path.write_text(json.dumps(payload, ensure_ascii=False),
                         encoding="utf-8")

    upcoming_path = PUBLIC_DIR / "upcoming.json"
    # MOPS（mops.twse.com.tw）在雲端資料中心 IP 會連線逾時（同 Investing/FRED），
    # 抓到 0／遠少於既有版本時，保留已提交的 upcoming.json，
    # 避免行事曆的「未收錄場次（淡色）」整批消失。本機（家用 IP）建好後提交即可。
    prev_count = 0
    if upcoming_path.exists():
        try:
            prev_count = json.loads(
                upcoming_path.read_text(encoding="utf-8")).get("count", 0)
        except (ValueError, OSError):
            prev_count = 0
    if prev_count > 0 and len(upcoming) < prev_count * 0.8:
        print(f"upcoming：本次僅抓到 {len(upcoming)} 筆（疑似 MOPS 擋雲端 IP），"
              f"保留既有已提交的 {prev_count} 筆，不覆蓋")
    else:
        for u in upcoming:
            u["market_cap"] = caps.get(u["code"], 0)
        upcoming_payload = {
            "generated_at": generated_at,
            "count": len(upcoming),
            "items": upcoming,
        }
        upcoming_path.write_text(json.dumps(upcoming_payload, ensure_ascii=False),
                                 encoding="utf-8")

    write_seo_files(list_items, details)

    print(f"共 {len(list_items)} 筆")
    for li in list_items:
        print(f"  {li['date']}  {li['code'] or '----':>4}  {li['company']}"
              f"  市值 {li['market_cap']:,} 億"
              f"  逐字稿 {li['transcript_chars']:,} 字")
    no_cap = sum(1 for li in list_items if not li["market_cap"]) \
        + sum(1 for u in upcoming if not u.get("market_cap"))
    print(f"list.json：{list_path}（{list_path.stat().st_size:,} bytes）")
    print(f"detail/：{len(list_items)} 個檔案")
    print(f"upcoming.json：{len(upcoming)} 筆 MOPS 公告場次"
          f"（{upcoming_path.stat().st_size:,} bytes）")
    if no_cap:
        print(f"註：{no_cap} 筆查無市值（market_cap=0，排序置後）")


if __name__ == "__main__":
    main()
