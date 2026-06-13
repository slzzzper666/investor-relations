"""階段一：MOPS 法說會爬蟲。

資料來源：公開資訊觀測站（舊版 ajax 端點，結構穩定）
  https://mopsov.twse.com.tw/mops/web/ajax_t100sb02_1
涵蓋上市(sii)與上櫃(otc)，依日期過濾出目標日的法說會。
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

from ir.logger import get_logger
from ir.net import get_session

log = get_logger("ir.mops")

AJAX_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t100sb02_1"
DOWNLOAD_URL = "https://mopsov.twse.com.tw/server-java/FileDownLoad"
DOWNLOAD_FIELDS = {
    "step": "9",
    "filePath": "/home/html/nas/STR/",
    "functionName": "t100sb02_1",
}


@dataclass
class Conference:
    stock_code: str
    company_name: str
    market: str               # sii=上市, otc=上櫃
    date: date
    time: str
    location: str
    summary: str              # 擇要訊息
    pdf_filename: str = ""    # 中文簡報檔名，如 121620260604M001.pdf
    website: str = ""         # 公司網站法說會頁
    video_urls: list[str] = field(default_factory=list)  # MOPS 登載的影音連結

    @property
    def pdf_url(self) -> str:
        """可直接點開的 PDF 連結（GET 版下載網址）。"""
        if not self.pdf_filename:
            return ""
        return (f"{DOWNLOAD_URL}?step=9&filePath=/home/html/nas/STR/"
                f"&fileName={self.pdf_filename}&functionName=t100sb02_1")


def _roc(d: date) -> tuple[str, str]:
    return str(d.year - 1911), f"{d.month:02d}"


def _parse_date(roc_str: str) -> date | None:
    """'115/06/05' → date(2026, 6, 5)"""
    try:
        y, m, d = roc_str.strip().split("/")
        return date(int(y) + 1911, int(m), int(d))
    except (ValueError, AttributeError):
        return None


def _fetch_month(market: str, target: date) -> list[Conference]:
    year, month = _roc(target)
    s = get_session()
    r = s.post(AJAX_URL, data={
        "encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
        "TYPEK": market, "year": year, "month": month,
    }, timeout=60)
    r.raise_for_status()
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "lxml")

    results: list[Conference] = []
    table = soup.find("table")
    if table is None:
        log.warning("MOPS %s %s/%s 查無表格（當月可能無資料）", market, year, month)
        return results

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 11:
            continue
        conf_date = _parse_date(tds[2].get_text(strip=True))
        if conf_date is None:
            continue

        pdf_filename = ""
        a = tds[6].find("a")
        if a and a.get_text(strip=True).lower().endswith(".pdf"):
            pdf_filename = a.get_text(strip=True)

        website_a = tds[8].find("a")
        video_urls = [a.get("href") for a in tds[9].find_all("a") if a.get("href")]

        results.append(Conference(
            stock_code=tds[0].get_text(strip=True),
            company_name=tds[1].get_text(strip=True),
            market=market,
            date=conf_date,
            time=tds[3].get_text(strip=True),
            location=tds[4].get_text(strip=True),
            summary=tds[5].get_text(strip=True),
            pdf_filename=pdf_filename,
            website=website_a.get("href", "") if website_a else "",
            video_urls=video_urls,
        ))
    return results


def get_conferences(target: date) -> list[Conference]:
    """抓取目標日期（含上市+上櫃）的所有法說會。"""
    confs: list[Conference] = []
    for market in ("sii", "otc"):
        month_rows = _fetch_month(market, target)
        day_rows = [c for c in month_rows if c.date == target]
        log.info("MOPS %s：當月 %d 筆，%s 共 %d 筆",
                 market, len(month_rows), target.isoformat(), len(day_rows))
        confs.extend(day_rows)
    return confs


def get_conferences_in_range(start: date, end: date) -> list[Conference]:
    """抓取 start~end 區間（含上市+上櫃）的所有法說會。

    供財報雷達子系統產生台股行事曆用：自動涵蓋跨月份，依代號+日期去重。
    """
    months: list[date] = []
    cur = start.replace(day=1)
    while cur <= end:
        months.append(cur)
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)

    seen: set[tuple[str, date]] = set()
    confs: list[Conference] = []
    for market in ("sii", "otc"):
        for m in months:
            for c in _fetch_month(market, m):
                if start <= c.date <= end and (c.stock_code, c.date) not in seen:
                    seen.add((c.stock_code, c.date))
                    confs.append(c)
    log.info("MOPS 區間 %s~%s：法說會共 %d 場", start.isoformat(), end.isoformat(),
             len(confs))
    return confs


def download_pdf(conf: Conference, dest_dir: Path) -> Path | None:
    """下載中文簡報 PDF，回傳檔案路徑；無檔案則回傳 None。"""
    if not conf.pdf_filename:
        return None
    dest = dest_dir / conf.pdf_filename
    if dest.exists():
        return dest
    s = get_session()
    r = s.post(DOWNLOAD_URL, data={**DOWNLOAD_FIELDS, "fileName": conf.pdf_filename},
               timeout=120)
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        log.warning("PDF 下載失敗 %s（status=%s）", conf.pdf_filename, r.status_code)
        return None
    dest.write_bytes(r.content)
    log.info("PDF 已下載：%s（%.1f KB）", dest.name, len(r.content) / 1024)
    return dest
