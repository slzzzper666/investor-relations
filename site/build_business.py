"""業務項目建置：近 N 天有法說會的公司 → AI 讀簡報 → data/business/{code}.json。

業務組合是「公司」的屬性而非「單場法說會」的屬性，所以以股票代號存檔，
同一家公司再開法說會就更新同一個檔（帶 as_of 期別，看得出資料新舊）。

名單來源刻意用 site/public/list.json（＝站上實際收錄的場次），不是直接掃 MOPS：
MOPS 公告的場次有一部分從沒進 Notion（無簡報／抓取失敗的小型股），
對那些公司做萃取等於白燒 AI 額度，做出來也沒有頁面可以顯示。

用法：
  python site/build_business.py            近 7 天、跳過已建檔的公司
  python site/build_business.py --days 14  改抓近 14 天
  python site/build_business.py --limit 20 這次最多做 20 家（分批燒額度）
  python site/build_business.py --force    已建檔的也重做
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
BUSINESS_DIR = ROOT_DIR / "data" / "business"
LIST_JSON = BASE_DIR / "public" / "list.json"

sys.path.insert(0, str(ROOT_DIR))
from ir.business import extract          # noqa: E402
from ir.logger import get_logger         # noqa: E402
from ir.net import get_session           # noqa: E402

log = get_logger("build_business")
TAIPEI = timezone(timedelta(hours=8))
DATA_DIR = ROOT_DIR / "data"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"


def _recent_companies(days: int) -> list[dict]:
    """站上近 days 天的場次，依公司去重（留最新一場）、市值大→小。"""
    if not LIST_JSON.exists():
        raise SystemExit(f"找不到 {LIST_JSON}，請先跑 site/build_data.py")
    data = json.loads(LIST_JSON.read_text(encoding="utf-8"))
    cutoff = (datetime.now(TAIPEI).date() - timedelta(days=days)).isoformat()

    best: dict[str, dict] = {}
    for it in data.get("items", []):
        code = it.get("code") or ""
        if not re.fullmatch(r"\d{4}", code) or it.get("date", "") < cutoff:
            continue
        cur = best.get(code)
        if cur is None or it["date"] > cur["date"]:
            best[code] = it
    return sorted(best.values(), key=lambda x: -(x.get("market_cap") or 0))


def _download_pdf(url: str, code: str, date: str) -> Path | None:
    """從 list.json 的 pdf_url 取簡報（MOPS GET 下載網址）。

    MOPS 對連續下載會回 502／空回應（暫時性），退避重試可救回大部分；
    不重試的話一輪下來會有七成公司誤判成「無簡報」。
    """
    if not url:
        return None
    m = re.search(r"fileName=([^&]+)", url)
    dest = DATA_DIR / (m.group(1) if m else f"{code}_{date}.pdf")
    if dest.exists() and dest.stat().st_size > 1000:
        return dest

    last = ""
    for attempt in range(3):
        try:
            r = get_session().get(url, timeout=120)
            if r.status_code == 200 and r.content.startswith(b"%PDF"):
                dest.write_bytes(r.content)
                return dest
            last = f"status={r.status_code}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)[:80]
        time.sleep(3 * (attempt + 1))
    log.warning("PDF 下載失敗 %s（%s）", code, last)
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="業務項目建置")
    ap.add_argument("--days", type=int, default=7, help="回溯天數（預設 7）")
    ap.add_argument("--limit", type=int, default=0, help="這次最多處理幾家")
    ap.add_argument("--force", action="store_true", help="已建檔的也重做")
    ap.add_argument("--retry-empty", action="store_true",
                    help="只重做「查過但沒抓到營收結構」的公司（換更強的模型時用）")
    args = ap.parse_args()

    BUSINESS_DIR.mkdir(parents=True, exist_ok=True)
    companies = _recent_companies(args.days)
    log.info("站上近 %d 天共 %d 家公司", args.days, len(companies))

    done = skipped = failed = no_source = empty = 0
    for it in companies:
        code, name, date = it["code"], it["company"], it["date"]
        out_file = BUSINESS_DIR / f"{code}.json"
        if out_file.exists() and not args.force:
            # --retry-empty：只回頭補「查過但沒抓到營收結構」的，其餘照樣跳過。
            # 較弱的模型會漏讀圖表，額度充裕時可用強模型再掃一次這批。
            if not args.retry_empty:
                skipped += 1
                continue
            try:
                prev = json.loads(out_file.read_text(encoding="utf-8"))
            except ValueError:
                prev = {}
            if prev.get("segments"):
                skipped += 1
                continue
        if args.limit and done >= args.limit:
            log.info("已達本次上限 %d 家，其餘留待下一輪", args.limit)
            break

        tag = f"{code} {name}"
        pdf_path = _download_pdf(it.get("pdf_url", ""), code, date)
        transcript = ""
        if pdf_path is None:
            t_file = TRANSCRIPT_DIR / f"{code}_{date}.txt"
            if t_file.exists():
                transcript = t_file.read_text(encoding="utf-8")
        if pdf_path is None and not transcript:
            log.info("%s：無簡報也無逐字稿，跳過", tag)
            no_source += 1
            continue

        try:
            profile = extract(name, code, pdf_path=pdf_path,
                              transcript=transcript)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            log.warning("%s：萃取失敗 %s", tag, msg[:160])
            failed += 1
            if "額度" in msg or "quota" in msg.lower():
                log.warning("AI 額度耗盡，中止本輪（已完成的都已存檔，下次接著跑）")
                break
            continue

        # 存空檔也有意義：記錄「查過了、簡報裡沒有營收結構」，下輪不重試白燒額度
        if not profile["segments"]:
            empty += 1
            log.info("%s：簡報中找不到營收結構", tag)
        out_file.write_text(json.dumps({
            "code": code,
            "name": name,
            "conf_date": date,
            "source": "pdf" if pdf_path is not None else "transcript",
            "built_at": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M"),
            **profile,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        done += 1
        time.sleep(1)
        log.info("%s：已建檔（%d 項｜%s）", tag, len(profile["segments"]),
                 "、".join(profile["tags"]) or "無標籤")

    log.info("===== 完成：新建 %d（其中無結構 %d）、略過 %d、無來源 %d、失敗 %d =====",
             done, empty, skipped, no_source, failed)


if __name__ == "__main__":
    main()
