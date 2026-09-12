"""逐字稿積壓回補：站上「有 MOPS 影音連結、卻沒逐字稿」的場次，補轉錄並重新分析。

背景：2026-06-17 起 irconference 改 https 轉址、加上錄影比每日管線晚上傳，
三個月內約 300 多場有錄影的場次只有簡報版分析。每日管線現已會回補前 3 天，
這支專門清歷史積壓。

STT 順序刻意與 ir.stt 不同：**Groq whisper 先（免費、快、不占 GPU）→ 額度用盡
換本機 GPU whisper**；不走 Gemini 音檔轉錄，把 Gemini 額度留給分析。
冪等、可中斷：每場做完立刻更新 Notion，重跑會自動跳過已有逐字稿的。

用法：
  python backfill_transcripts.py --since 2026-06-17 --until 2026-09-08 --dry-run
  python backfill_transcripts.py --since 2026-06-17 --until 2026-09-08 [--limit N]
"""
import argparse
import json
from datetime import date

import config
from ir import stt
from ir.analyze import analyze
from ir.logger import get_logger
from ir.media import get_audio
from ir.mops import get_conferences_in_range
from ir.notion_db import status as notion_status, upsert_conference
from ir.segment import plan_segments

log = get_logger("ir.backfill_tr")
SITE = config.BASE_DIR / "site" / "public"

_groq_ok = bool(config.GROQ_API_KEY)


def _transcribe(audio_path) -> str:
    """Groq 優先；429/額度錯誤後本次執行改走本機 GPU。"""
    global _groq_ok
    if _groq_ok:
        try:
            size_mb = audio_path.stat().st_size / 1e6
            text = (stt._transcribe_groq(audio_path) if size_mb < 24
                    else stt._transcribe_groq_chunked(audio_path))
            return stt._collapse_loops(text)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "429" in msg or "rate" in msg.lower() or "limit" in msg.lower():
                _groq_ok = False
                log.warning("Groq 額度用盡，改用本機 GPU whisper：%s", msg[:100])
            else:
                log.warning("Groq 轉錄失敗（%s），改用本機", msg[:100])
    return stt._transcribe_local(audio_path)


def _targets(since: str, until: str) -> list:
    """站上 transcript_chars=0 且 MOPS 有登載影音的場次（Conference 物件）。"""
    items = json.loads((SITE / "list.json").read_text(encoding="utf-8"))["items"]
    want = {(x["code"], x["date"]): x.get("market_cap") or 0 for x in items
            if since <= x["date"] <= until and not x.get("transcript_chars")
            and x.get("code")}
    confs = get_conferences_in_range(date.fromisoformat(since), date.fromisoformat(until))
    out = [c for c in confs
           if (c.stock_code, c.date.isoformat()) in want and c.video_urls]
    # 市值大→小：最多人看的頁面先有逐字稿
    out.sort(key=lambda c: -want[(c.stock_code, c.date.isoformat())])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="逐字稿積壓回補")
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default=date.today().isoformat())
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    targets = _targets(args.since, args.until)
    log.info("%s ~ %s：有影音、無逐字稿的場次 %d 場", args.since, args.until, len(targets))
    if args.dry_run:
        for c in targets[:30]:
            log.info("  %s %s %s  %s", c.date, c.stock_code, c.company_name,
                     (c.video_urls or [""])[0][:60])
        return

    ok = skip = no_audio = fail = 0
    for c in targets:
        if args.limit and ok >= args.limit:
            break
        tag = f"{c.stock_code} {c.company_name} {c.date}"
        try:
            st = notion_status(c)
            if st is None or st["has_transcript"]:
                skip += 1
                continue
            audio_path, video_url = get_audio(c, config.AUDIO_DIR)
            if not audio_path:
                no_audio += 1
                log.info("%s：抓不到音檔（錄影已下架或非直接連結）", tag)
                continue
            t_file = config.TRANSCRIPT_DIR / f"{c.stock_code}_{c.date.isoformat()}.txt"
            if t_file.exists() and t_file.stat().st_size > 300:
                transcript = t_file.read_text(encoding="utf-8")
            else:
                transcript = _transcribe(audio_path)
                t_file.write_text(transcript, encoding="utf-8")
            analysis = analyze(c.company_name, c.stock_code, c.date.isoformat(),
                               transcript=transcript)
            try:
                plan = plan_segments(c.company_name, c.stock_code, c.date.isoformat(), transcript)
            except Exception as e:  # noqa: BLE001
                log.warning("%s：分段失敗 %s", tag, str(e)[:100])
                plan = None
            upsert_conference(c, analysis, transcript, video_url, segments=plan)
            ok += 1
            log.info("%s：逐字稿 %d 字，已更新 Notion（%d/%d）", tag, len(transcript),
                     ok, len(targets))
        except KeyboardInterrupt:
            break
        except Exception as e:  # noqa: BLE001
            fail += 1
            log.warning("%s：失敗 %s", tag, str(e)[:160])
            if "額度" in str(e) and "Gemini" in str(e):
                log.warning("Gemini 分析額度耗盡，中止；逐字稿已存檔，下次只需重跑分析")
                break

    log.info("===== 完成：補上 %d、跳過 %d、無音檔 %d、失敗 %d =====", ok, skip, no_audio, fail)


if __name__ == "__main__":
    main()
