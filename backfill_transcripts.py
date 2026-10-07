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
from ir.proofread import proofread
from ir.segment import plan_segments
from ir.stt_hints import hints_for

log = get_logger("ir.backfill_tr")
SITE = config.BASE_DIR / "site" / "public"

_groq_ok = bool(config.GROQ_API_KEY)


def _transcribe(audio_path, hints=None) -> str:
    """Groq 優先；429/額度錯誤後本次執行改走本機 GPU。hints：語言＋提示詞（ir/stt_hints）。"""
    global _groq_ok
    if _groq_ok:
        try:
            size_mb = audio_path.stat().st_size / 1e6
            text = (stt._transcribe_groq(audio_path, hints) if size_mb < 24
                    else stt._transcribe_groq_chunked(audio_path, hints=hints))
            return stt._collapse_loops(text)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "429" in msg or "rate" in msg.lower() or "limit" in msg.lower():
                _groq_ok = False
                log.warning("Groq 額度用盡，改用本機 GPU whisper：%s", msg[:100])
            else:
                log.warning("Groq 轉錄失敗（%s），改用本機", msg[:100])
    return stt._transcribe_local(audio_path, hints)


def _targets(since: str, until: str, ids: set[str] | None = None) -> list:
    """站上 transcript_chars=0 且 MOPS 有登載影音的場次（Conference 物件）。

    ids：改為指定這些場次（{code}_{date}），不管有沒有逐字稿——重聽用。
    """
    items = json.loads((SITE / "list.json").read_text(encoding="utf-8"))["items"]
    want = {(x["code"], x["date"]): x.get("market_cap") or 0 for x in items
            if since <= x["date"] <= until and x.get("code")
            and (x["id"] in ids if ids else not x.get("transcript_chars"))}
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
    ap.add_argument("--ids-file", default="",
                    help="只重做檔案裡列的場次（每行 {code}_{date}），已有逐字稿也重聽——"
                         "用於 Whisper 誤判語言、亂翻英文的舊稿")
    ap.add_argument("--shard", default="", help="平行分工 K/N：只做第 K 份（0 起算），例如 0/3")
    ap.add_argument("--reverse", action="store_true",
                    help="從清單尾端往前做：與同一份的正向程序兩頭夾擊、在中間會合"
                         "（每場開工前都先查 Notion，已補的會跳過）")
    args = ap.parse_args()

    ids = None
    if args.ids_file:
        ids = {l.strip() for l in open(args.ids_file, encoding="utf-8") if l.strip()}
    targets = _targets(args.since, args.until, ids)
    if args.shard:                       # 多個程序各做一份，互不重疊
        k, n = map(int, args.shard.split("/"))
        targets = targets[k::n]
        log.info("分工 %d/%d", k, n)
    if args.reverse:
        targets = targets[::-1]
        log.info("反向：從尾端往前做")
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
            if st is None or (st["has_transcript"] and not ids):
                skip += 1
                continue
            t_old = config.TRANSCRIPT_DIR / f"{c.stock_code}_{c.date.isoformat()}.txt"
            if ids and t_old.exists():
                t_old.unlink()                    # 重聽：舊的本機快取稿就是要換掉的那份
            audio_path, video_url = get_audio(c, config.AUDIO_DIR)
            if not audio_path:
                no_audio += 1
                log.info("%s：抓不到音檔（錄影已下架或非直接連結）", tag)
                continue
            t_file = config.TRANSCRIPT_DIR / f"{c.stock_code}_{c.date.isoformat()}.txt"
            if t_file.exists() and t_file.stat().st_size > 300:
                transcript = t_file.read_text(encoding="utf-8")
            else:
                hints = hints_for(c.company_name, c.stock_code, c.date.isoformat(), video_url)
                transcript = _transcribe(audio_path, hints)
                try:
                    transcript, _rep = proofread(transcript, c.company_name, c.stock_code,
                                                 c.date.isoformat(),
                                                 also=[x.company_name for x in targets if x.date == c.date])
                except Exception as e:  # noqa: BLE001
                    log.warning("%s：文字校對略過（%s）", tag, str(e)[:100])
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
