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
BUSINESS_DIR = ROOT_DIR / "data" / "business"   # 業務項目（AI 讀簡報產出，見 build_business.py）
EXCLUDED_FILE = ROOT_DIR / "data" / "excluded_ids.txt"  # 壞源（假法說會）排除清單


def _load_excluded() -> set:
    """讀排除清單（每行一個 {code}_{date}，# 開頭為註解）。"""
    if not EXCLUDED_FILE.exists():
        return set()
    out = set()
    for ln in EXCLUDED_FILE.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.add(ln)
    return out
MCAP_CACHE = BASE_DIR / ".mcap_cache.json"
FIN_CACHE = BASE_DIR / ".fin6q_cache.json"
PE_CACHE = BASE_DIR / ".pe_cache.json"

# 重用 ir 套件（MOPS 端點常數、ROC 日期解析、含 Proxy 偵測的 Session）
sys.path.insert(0, str(ROOT_DIR))
from ir.fin6q import QUARTERS, RateLimited   # noqa: E402
from ir.fin6q import fetch as fetch_quarters  # noqa: E402
from ir.industry import (fetch_industry_map, industry_name,  # noqa: E402
                         peer_pe_stats)
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


def _cell(v, fmt: str = "{:,.2f}") -> str:
    return "—" if v is None else fmt.format(v)


def _fin_static_html(fin: dict | None) -> str:
    """純靜態頁的財報數據區塊（近 N 季表格 + 估值，供 SEO 收錄）。"""
    if not fin:
        return ""
    qs = fin.get("quarters") or []
    head = []
    if fin.get("market_cap"):
        head.append(f"<li>市值：約 {fin['market_cap']:,} 億元</li>")
    if fin.get("industry"):
        head.append(f"<li>產業：{_esc(fin['industry'])}</li>")
    if fin.get("pe") is not None:
        peer = fin.get("industry_pe") or {}
        tail = ""
        if peer.get("median"):
            # 與 detail.js 的 peerPeSub 同一套規則：差距逾一倍改用倍數，
            # 免得微利股出現「溢價 9852%」這種沒有意義的數字
            ratio = fin["pe"] / peer["median"]
            gap = (f"{ratio:.1f} 倍於同業" if ratio >= 2 or ratio <= 0.5
                   else f"{'溢價' if ratio >= 1 else '折價'} "
                        f"{abs(ratio - 1) * 100:.0f}%")
            tail = (f"（{_esc(fin.get('industry', '同業'))}中位數 "
                    f"{peer['median']:.1f} 倍、樣本 {peer['n']} 檔，本檔{gap}）")
        head.append(f"<li>本益比：{fin['pe']:.1f} 倍{tail}</li>")

    table = ""
    if qs:
        ths = "".join(f"<th>{_esc(q['period'])}</th>" for q in qs)

        def row(label: str, key: str, fmt: str = "{:,.2f}") -> str:
            tds = "".join(f"<td>{_cell(q.get(key), fmt)}</td>" for q in qs)
            return f"<tr><th scope='row'>{label}</th>{tds}</tr>"

        table = (
            f"<table><caption>近 {len(qs)} 季單季財務數據</caption>"
            f"<thead><tr><th scope='row'>項目</th>{ths}</tr></thead><tbody>"
            + row("營收（億元）", "revenue")
            + row("營收年增率（%）", "revenue_yoy", "{:+.1f}")
            + row("EPS（元）", "eps")
            + row("EPS 年增率（%）", "eps_yoy", "{:+.1f}")
            + row("毛利率（%）", "gross_margin", "{:.1f}")
            + row("資本支出（億元）", "capex")
            + "</tbody></table>")

    # 財務報告書傳送門：公開資訊觀測站電子書清單（各季合併／個體報表 PDF）。
    # PDF 本身要走站方 JS 二次請求，無法直接深連結，連清單頁最穩。
    link = ""
    code, year = fin.get("code"), fin.get("year")
    if code and year:
        url = (f"https://doc.twse.com.tw/server-java/t57sb01?step=1&colorchg=1"
               f"&co_id={code}&year={year - 1911}&seamon=&mtype=A&")
        link = (f"<p><a href='{url}' rel='nofollow noopener' target='_blank'>"
                f"財務報告書（各季三表 PDF）</a>　公開資訊觀測站 {year - 1911} 年度</p>")

    if not head and not table and not link:
        return ""
    ul = f"<ul>{''.join(head)}</ul>" if head else ""
    return f"<h2>財務數據</h2>{ul}{table}{link}"


def _business_static_html(biz: dict | None) -> str:
    """純靜態頁的業務項目區塊。"""
    if not biz or not biz.get("segments"):
        return ""
    lis = []
    for s in biz["segments"]:
        pct = f"：{s['pct']:.1f}%" if s.get("pct") is not None else ""
        note = f"（{_esc(s['note'])}）" if s.get("note") else ""
        lis.append(f"<li>{_esc(s['name'])}{pct}{note}</li>")
    tags = ""
    if biz.get("tags"):
        tags = ("<p>族群："
                + "、".join(_esc(t) for t in biz["tags"]) + "</p>")
    as_of = f"（{_esc(biz['as_of'])}）" if biz.get("as_of") else ""
    return (f"<h2>業務項目與營收比重{as_of}</h2>"
            f"<ul>{''.join(lis)}</ul>{tags}")


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

    # 財報區塊需要代號與年份組財務報告書連結；沒有財報數據的公司也給傳送門
    fin_static = None
    if d.get("financials") or re.fullmatch(r"\d{4}", d.get("code") or ""):
        fin_static = {**(d.get("financials") or {}),
                      "code": d.get("code"),
                      "year": int(d["date"][:4]) if d.get("date") else None}

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
{_business_static_html(d.get('business'))}
{_fin_static_html(fin_static)}
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


def _latest_period(today) -> str:
    """依台股法定財報截止日，推算目前最可能已公布的最新一季，如 '2026 Q2'。

    只用來當快取鍵：同一季內不重抓，跨季才更新。
    """
    y, md = today.year, (today.month, today.day)
    if md >= (11, 14):
        return f"{y} Q3"
    if md >= (8, 14):
        return f"{y} Q2"
    if md >= (5, 15):
        return f"{y} Q1"
    if md >= (3, 31):
        return f"{y - 1} Q4"
    return f"{y - 1} Q3"


def fetch_tw_financials(code: str, cache: dict) -> list[dict] | None:
    """單檔台股近 N 季財報序列（新→舊），來源 FinMind（見 ir/fin6q.py）。

    依「目前最新已公布季」快取於 .fin6q_cache.json，同季不重抓。
    市值、本益比為股價型（每日變動），不在此處，於組裝階段注入。
    """
    if not re.fullmatch(r"\d{4}", code or ""):
        return None

    period_key = _latest_period(datetime.now(TAIPEI).date())
    cached = cache.get(code)
    if cached and cached.get("v") == 4 and cached.get("period_key") == period_key:
        return cached.get("quarters")

    quarters = fetch_quarters(code)          # RateLimited 由呼叫端處理
    cache[code] = {"v": 4, "period_key": period_key, "quarters": quarters}
    return quarters


def _load_business(code: str) -> dict | None:
    """業務項目（data/business/{code}.json，由 build_business.py 產出）。"""
    if not code:
        return None
    f = BUSINESS_DIR / f"{code}.json"
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if not d.get("segments") and not d.get("tags"):
        return None          # 查過但簡報裡沒有營收結構 → 前端不顯示空框
    return {k: d.get(k) for k in
            ("segments", "tags", "as_of", "confidence", "conf_date", "source")}


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
    industries = fetch_industry_map(get_session())
    ind_pe = peer_pe_stats(pes, industries)
    fin_cache = _load_fin_cache()
    # 財報序列逐檔抓 FinMind。雲端(GitHub Actions)設 IR_SKIP_FINANCIALS=1 →
    # 全用已提交的 .fin6q_cache.json、完全不連外（部署快、也不消耗 FinMind 免費額度）；
    # 本機不設此旗標，照常補抓缺漏並更新快取，跑完 commit 快取即可。
    fetch_ok = not os.getenv("IR_SKIP_FINANCIALS")
    if not fetch_ok:
        print("IR_SKIP_FINANCIALS 已設：財報全用既有快取、不連 FinMind（雲端部署加速）")
    # FinMind 免費版有每小時請求上限，且一檔要兩次請求（損益表＋現金流量表）。
    # 全站上千檔不可能一輪抓完 → 每輪限量、依日期新到舊優先補（讀者最常看近期場次），
    # 快取跨輪累積，跑個幾輪就補滿。額度中途用盡也會自動改吃快取，不會讓整個 build 失敗。
    fin_budget = int(os.getenv("IR_FIN_LIMIT", "120"))
    fin_fetched = 0
    fin_period_key = _latest_period(datetime.now(TAIPEI).date())

    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    DETAIL_DIR.mkdir(parents=True, exist_ok=True)
    for f in DETAIL_DIR.glob("*.json"):  # 只清台股，保留美股 us-* 由 build_us.py 管
        if not f.name.startswith("us-"):
            f.unlink()

    used_ids: set[str] = set()
    list_items: list[dict] = []
    details: list[dict] = []
    excluded = _load_excluded()
    if excluded:
        print(f"排除清單：{len(excluded)} 場（壞源假法說會）不納入網站")
    n_excluded = 0
    for it in items:
        it_id = make_id(it, used_ids)
        if it_id in excluded:          # 第三方影片冒充法說會 → 整筆不上站
            n_excluded += 1
            continue
        detail = {"id": it_id, **it}
        code = it["code"]
        cached = fin_cache.get(code)
        is_fresh = (cached and cached.get("v") == 4
                    and cached.get("period_key") == fin_period_key)
        if fetch_ok and not is_fresh and fin_fetched < fin_budget:
            try:
                quarters = fetch_tw_financials(code, fin_cache)
                fin_fetched += 1
            except RateLimited as exc:
                fetch_ok = False
                quarters = cached.get("quarters") if cached else None
                print(f"FinMind 額度用盡（{exc}），其餘改用既有快取、不再連線")
            except Exception as exc:  # noqa: BLE001
                quarters = cached.get("quarters") if cached else None
                print(f"財報 {code} 抓取失敗：{exc}")
        else:
            quarters = cached.get("quarters") if cached else None

        ind = industries.get(code, "")
        # 股價型數據（市值、本益比）每日變動，不進快取，於此注入
        fin = {
            "quarters": quarters or [],
            "market_cap": caps.get(code) or None,
            "pe": pes.get(code),
            "industry": industry_name(ind),
            "industry_code": ind,
            "industry_pe": ind_pe.get(ind),
        }
        detail["financials"] = fin if (quarters or fin["market_cap"]) else None
        business = _load_business(code)
        if business:
            detail["business"] = business
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
            "industry": industry_name(ind),
            "tags": (business or {}).get("tags") or [],
        })

    FIN_CACHE.write_text(json.dumps(fin_cache, ensure_ascii=False),
                         encoding="utf-8")
    fin_n = sum(1 for d in details
                if (d.get("financials") or {}).get("quarters"))
    biz_n = sum(1 for d in details if d.get("business"))
    print(f"財報序列：{fin_n}/{len(details)} 筆有近 {QUARTERS} 季數據"
          f"（本輪新抓 {fin_fetched} 檔）")
    print(f"業務項目：{biz_n} 筆有資料")

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
