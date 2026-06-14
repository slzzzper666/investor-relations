"""歷史法說會補檔（本地 Whisper STT，背景長時間執行）。

策略：
  - 月份由新到舊（6→1 月），同月內依市值由大到小（重要公司先補）
  - 只做：下載音檔→本地 Whisper 逐字稿→存檔備料（不分析、不標記完成）
  - AI 分析改由 Claude 額度處理：claude_prep.py 出批 → Claude 分析 → claude_push.py 進 Notion
    （Gemini/Groq 免費額度不足，故分析全走 Claude）
  - 不推播 TG/DC（歷史資料，避免洗版）
  - 逐字稿已備檔者自動跳過，可隨時中斷後重跑接續

用法：
  .venv\\Scripts\\python backfill_history.py
"""
import json
from datetime import date

import config
from ir.analyze import AnalysisQuotaExhausted
from ir.logger import get_logger
from ir.media import get_audio
from ir.mops import _fetch_month
from ir.stt import transcribe
from main import _load_processed, _mark_processed, process_one

log = get_logger("ir.backfill")


def _bank_transcript_only(conf) -> None:
    """分析額度耗盡時：只用 GPU 把有影音的場次轉成逐字稿存檔，留待之後分析。"""
    t_file = config.TRANSCRIPT_DIR / f"{conf.stock_code}_{conf.date.isoformat()}.txt"
    if t_file.exists():
        return
    audio, _ = get_audio(conf, config.AUDIO_DIR)
    if not audio:
        return  # 純 PDF 場次留給分析階段（很快），不在此處理
    text = transcribe(audio)
    t_file.write_text(text, encoding="utf-8")
    log.info("逐字稿已備檔（待分析）：%s %s", conf.stock_code, conf.company_name)
    try:
        audio.unlink()  # 轉完刪音檔省空間
    except OSError:
        pass

MCAP_CACHE = config.BASE_DIR / "site" / ".mcap_cache.json"
MONTHS = [(2026, m) for m in (6, 5, 4, 3, 2, 1)]  # 新到舊


def _load_mcap() -> dict[str, int]:
    if MCAP_CACHE.exists():
        try:
            return json.loads(MCAP_CACHE.read_text(encoding="utf-8")).get("caps", {})
        except (ValueError, KeyError):
            pass
    return {}


def _gather() -> list:
    today = date.today()
    processed = _load_processed()
    mcap = _load_mcap()
    seen: set[str] = set()
    work = []
    for year, month in MONTHS:
        anchor = date(year, month, 15)
        for market in ("sii", "otc"):
            try:
                rows = _fetch_month(market, anchor)
            except Exception as e:
                log.warning("MOPS %s %d-%02d 抓取失敗：%s", market, year, month, e)
                continue
            for c in rows:
                if c.date.year != year or c.date.month != month:
                    continue
                if c.date > today:
                    continue
                key = f"{c.stock_code}_{c.date.isoformat()}"
                if key in processed or key in seen:
                    continue
                seen.add(key)
                work.append(c)
    # 排序：月份新→舊，同月市值大→小
    work.sort(key=lambda c: ((c.date.year, c.date.month),
                             mcap.get(c.stock_code, 0), c.date), reverse=True)
    return work


def main() -> None:
    work = _gather()
    log.info("===== 歷史補檔開始：待處理 %d 場 =====", len(work))
    processed = _load_processed()
    ok = fail = banked = consec = 0
    quota_dead = True  # 一律只轉逐字稿備檔；AI 分析交給 Claude（claude_prep/push）
    for i, conf in enumerate(work, 1):
        key = f"{conf.stock_code}_{conf.date.isoformat()}"
        log.info("[%d/%d] %s %s（%s）%s", i, len(work), conf.stock_code,
                 conf.company_name, conf.date.isoformat(),
                 "[備檔模式]" if quota_dead else "")
        # 分析額度已耗盡 → 只用 GPU 備存逐字稿，不再嘗試分析
        if quota_dead:
            try:
                _bank_transcript_only(conf)
                banked += 1
            except Exception:
                log.exception("%s 逐字稿備檔失敗", conf.stock_code)
            continue
        try:
            process_one(conf, push=False)
            _mark_processed(key, processed)
            ok += 1
            consec = 0
        except AnalysisQuotaExhausted:
            log.warning("分析額度耗盡，切換為純逐字稿備檔模式（分析待明日額度重置或 Claude）")
            quota_dead = True
            try:
                _bank_transcript_only(conf)
                banked += 1
            except Exception:
                log.exception("%s 逐字稿備檔失敗", conf.stock_code)
        except Exception:
            log.exception("%s %s 處理失敗", conf.stock_code, conf.company_name)
            fail += 1
            consec += 1
            if consec >= 15:
                log.error("連續 %d 場失敗，停止本輪（隔天重跑接續）", consec)
                break
    log.info("===== 歷史補檔結束：成功 %d、備檔 %d、失敗 %d =====", ok, banked, fail)


if __name__ == "__main__":
    main()
