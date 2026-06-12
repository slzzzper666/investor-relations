"""法說會自動整理系統 — 主流程。

用法：
  python main.py                     # 處理「昨天」（Asia/Taipei）的法說會
  python main.py --date 2026-06-11  # 處理指定日期
  python main.py --limit 3          # 只處理前 N 家（測試用）
  python main.py --no-push          # 不推播（測試用）

流程：MOPS 爬蟲 → 影音抽取 → STT 逐字稿 → AI 分析 → Notion → TG/DC 推播
每家公司獨立容錯，單一公司失敗不影響其他公司。
"""
import argparse
import json
from datetime import date, datetime, timedelta

import config
from ir.logger import TAIPEI, get_logger
from ir.mops import Conference, download_pdf, get_conferences
from ir.media import get_audio
from ir.stt import transcribe
from ir.analyze import analyze
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


def process_one(conf: Conference, push: bool = True) -> bool:
    """處理單一場法說會，成功回傳 True。"""
    tag = f"{conf.stock_code} {conf.company_name}"
    transcript = ""
    video_url = ""

    # 影音 → 逐字稿（找不到影音就退用 PDF）
    audio_path, video_url = get_audio(conf, config.AUDIO_DIR)
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


def run(target: date, limit: int = 0, push: bool = True) -> None:
    log.info("===== 法說會整理開始：%s =====", target.isoformat())
    confs = get_conferences(target)
    if limit:
        confs = confs[:limit]
    if not confs:
        log.info("%s 沒有法說會，結束", target.isoformat())
        return

    processed = _load_processed()
    ok = fail = skip = 0
    for conf in confs:
        key = f"{conf.stock_code}_{conf.date.isoformat()}"
        if key in processed:
            log.info("%s %s：已處理過，跳過", conf.stock_code, conf.company_name)
            skip += 1
            continue
        try:
            process_one(conf, push=push)
            _mark_processed(key, processed)
            ok += 1
        except Exception:
            log.exception("%s %s 處理失敗", conf.stock_code, conf.company_name)
            fail += 1

    log.info("===== 完成：成功 %d、失敗 %d、跳過 %d（共 %d 場）=====",
             ok, fail, skip, len(confs))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="法說會自動整理系統")
    parser.add_argument("--date", help="目標日期 YYYY-MM-DD（預設＝台北時間的昨天）")
    parser.add_argument("--limit", type=int, default=0, help="只處理前 N 家（測試用）")
    parser.add_argument("--no-push", action="store_true", help="不推播 TG/DC")
    args = parser.parse_args()

    if args.date:
        target = date.fromisoformat(args.date)
    else:
        target = (datetime.now(TAIPEI) - timedelta(days=1)).date()
    run(target, limit=args.limit, push=not args.no_push)
