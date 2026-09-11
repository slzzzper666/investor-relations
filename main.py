"""法說會自動整理系統 — 主流程。

用法：
  python main.py                     # 處理「昨天」（Asia/Taipei）的法說會
  python main.py --date 2026-06-11  # 處理指定日期
  python main.py --limit 3          # 只處理前 N 家（測試用）
  python main.py --no-push          # 不推播（測試用）
  python main.py --lookback 0       # 只處理目標日、不回補前幾天

流程：MOPS 爬蟲 → 影音抽取 → STT 逐字稿 → AI 分析 → Notion → TG/DC 推播
每家公司獨立容錯，單一公司失敗不影響其他公司。
"""
import argparse
import json
from datetime import date, datetime, timedelta

import config
from ir.logger import TAIPEI, get_logger
from ir.mops import (Conference, download_pdf, get_conferences,
                     get_conferences_in_range)
from ir.media import get_audio
from ir.stt import transcribe
from ir.analyze import analyze
from ir.notion_db import status as notion_status
from ir.notion_db import upsert_conference
from ir.notify import push_discord, push_telegram

log = get_logger("ir.main")

PROCESSED_FILE = config.DATA_DIR / "processed.json"


def _load_processed() -> set[str]:
    if PROCESSED_FILE.exists():
        return set(json.loads(PROCESSED_FILE.read_text(encoding="utf-8-sig")))
    return set()


def _mark_processed(key: str, processed: set[str]) -> None:
    processed.add(key)
    # 寫入前重新讀檔合併：避免多個程序同時跑時互相覆蓋對方的紀錄
    merged = _load_processed() | processed
    PROCESSED_FILE.write_text(json.dumps(sorted(merged), ensure_ascii=False,
                                         indent=1), encoding="utf-8")


def process_one(conf: Conference, push: bool = True, audio: bool = True) -> bool:
    """處理單一場法說會，成功回傳 True。

    audio=False 時跳過影音與 STT、直接用簡報分析（大量回補時先求有資料，
    逐字稿由另一個慢速流程補）。
    """
    tag = f"{conf.stock_code} {conf.company_name}"
    transcript = ""
    video_url = ""

    # 影音 → 逐字稿（找不到影音就退用 PDF）
    audio_path, video_url = get_audio(conf, config.AUDIO_DIR) if audio else (None, "")
    if audio_path:
        t_file = config.TRANSCRIPT_DIR / f"{conf.stock_code}_{conf.date.isoformat()}.txt"
        if t_file.exists():
            transcript = t_file.read_text(encoding="utf-8")
            log.info("%s：使用既有逐字稿（%d 字）", tag, len(transcript))
        else:
            try:
                transcript = transcribe(audio_path)
                t_file.write_text(transcript, encoding="utf-8")
            except Exception as e:
                log.warning("%s：STT 失敗（%s），降級用 PDF 分析", tag, e)
                transcript = ""
    if not video_url and conf.video_urls:
        video_url = conf.video_urls[0]  # 沒抓到音檔仍保留原始連結供參考

    # AI 分析（逐字稿優先，否則用 PDF；兩者皆無則只記錄基本資訊）
    if transcript:
        analysis = analyze(conf.company_name, conf.stock_code,
                           conf.date.isoformat(), transcript=transcript)
    else:
        pdf_path = download_pdf(conf, config.DATA_DIR)
        if pdf_path:
            analysis = analyze(conf.company_name, conf.stock_code,
                               conf.date.isoformat(), pdf_path=pdf_path)
        else:
            log.info("%s：無影音也無 PDF，僅記錄基本資訊", tag)
            analysis = {
                "one_liner": conf.summary or "尚未取得簡報與影音內容",
                "highlights": [f"時間：{conf.time}", f"地點：{conf.location}"],
                "outlook": "",
                "ai_view": "",
            }

    notion_url = upsert_conference(conf, analysis, transcript, video_url)
    if push:
        push_telegram(conf, analysis, video_url, notion_url)
        push_discord(conf, analysis, video_url, notion_url)
    return True


def enrich_transcript(conf: Conference) -> bool:
    """已在 Notion 但沒逐字稿的場次：錄影上傳了就補轉錄、重新分析、更新 Notion。

    irconference 的錄影通常在法說會當晚 03:00 前後才上傳，01:00 的每日執行
    抓不到；隔天回補時再試一次就有了。只更新 Notion、不重推 TG/DC。
    回傳是否補到。
    """
    tag = f"{conf.stock_code} {conf.company_name}"
    audio_path, video_url = get_audio(conf, config.AUDIO_DIR)
    if not audio_path:
        return False
    t_file = config.TRANSCRIPT_DIR / f"{conf.stock_code}_{conf.date.isoformat()}.txt"
    if t_file.exists():
        transcript = t_file.read_text(encoding="utf-8")
    else:
        transcript = transcribe(audio_path)
        t_file.write_text(transcript, encoding="utf-8")
    analysis = analyze(conf.company_name, conf.stock_code, conf.date.isoformat(),
                       transcript=transcript)
    upsert_conference(conf, analysis, transcript, video_url)
    log.info("%s：逐字稿已補上（%d 字）並重新分析", tag, len(transcript))
    return True


def run(target: date, limit: int = 0, push: bool = True, audio: bool = True,
        lookback: int = 3) -> None:
    """處理 target 當天，並順帶回補前 lookback 天「Notion 裡還沒有」的場次。

    Railway 容器每次都是全新的，processed.json 不保留；過去只跑「昨天」一次，
    任何原因失敗（API 下架、限流、逾時）就永遠缺一場——2026 年 7~9 月因此掉了
    124 場。回補以 Notion 為準判斷有沒有做過，補進來的一樣推播（晚到總比沒有好）。
    """
    start = target - timedelta(days=lookback)
    log.info("===== 法說會整理開始：%s（回補至 %s）=====",
             target.isoformat(), start.isoformat())
    confs = get_conferences_in_range(start, target) if lookback else get_conferences(target)
    # 當天的排前面、回補的排後面；同一天內維持 MOPS 順序
    confs.sort(key=lambda c: c.date != target)
    if limit:
        confs = confs[:limit]
    if not confs:
        log.info("%s 沒有法說會，結束", target.isoformat())
        return

    processed = _load_processed()
    ok = fail = skip = enriched = 0
    for conf in confs:
        key = f"{conf.stock_code}_{conf.date.isoformat()}"
        tag = f"{conf.stock_code} {conf.company_name}"
        if key in processed:
            log.info("%s：已處理過，跳過", tag)
            skip += 1
            continue
        # Notion 才是「做過沒」的真相：Railway 的 processed.json 每次都是空的，
        # 手動重跑同一天也靠這個避免重複推播（每場多一次查詢，約 0.3 秒）
        try:
            st = notion_status(conf)
        except Exception as e:  # noqa: BLE001
            log.warning("%s：查 Notion 失敗（%s），視為未處理", tag, e)
            st = None
        if st is not None:
            # 做過但沒逐字稿、且 MOPS 有登載影音 → 錄影可能已上傳，補逐字稿
            if (audio and conf.date != target and not st["has_transcript"]
                    and conf.video_urls):
                try:
                    if enrich_transcript(conf):
                        enriched += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("%s：補逐字稿失敗：%s", tag, str(e)[:160])
            skip += 1
            continue
        if conf.date != target:
            log.info("%s：%s 的缺漏場次，回補", tag, conf.date.isoformat())
        try:
            process_one(conf, push=push, audio=audio)
            _mark_processed(key, processed)
            ok += 1
        except Exception:
            log.exception("%s 處理失敗", tag)
            fail += 1

    log.info("===== 完成：成功 %d、失敗 %d、跳過 %d、補逐字稿 %d（共 %d 場）=====",
             ok, fail, skip, enriched, len(confs))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="法說會自動整理系統")
    parser.add_argument("--date", help="目標日期 YYYY-MM-DD（預設＝台北時間的昨天）")
    parser.add_argument("--limit", type=int, default=0, help="只處理前 N 家（測試用）")
    parser.add_argument("--no-push", action="store_true", help="不推播 TG/DC")
    parser.add_argument("--pdf-only", action="store_true",
                        help="跳過影音與 STT，直接用簡報分析（回補用）")
    parser.add_argument("--lookback", type=int, default=3,
                        help="順帶回補前 N 天 Notion 沒有的場次（預設 3，0 關閉）")
    args = parser.parse_args()

    if args.date:
        target = date.fromisoformat(args.date)
    else:
        target = (datetime.now(TAIPEI) - timedelta(days=1)).date()
    run(target, limit=args.limit, push=not args.no_push, audio=not args.pdf_only,
        lookback=args.lookback)
