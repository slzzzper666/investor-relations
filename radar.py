"""財報雷達子系統進入點（IR 專案）。

台美股財報行事曆/結果 + 總經數據事件提醒，推播到 TG/DC（共用 IR 推播金鑰）。

用法：
  python radar.py --test       立即執行一次完整測試（行事曆 + 結果）
  python radar.py --calendar   只推播行事曆（今日～下週五）
  python radar.py --result     只抓最新結果並推播
  python radar.py --daemon     常駐：07:00 台北時間推行事曆，每 15 分鐘輪詢結果
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta

import config
from ir.logger import get_logger
from ir.notify import push_text
from ir.radar import macro, tw, us
from ir.radar.formatter import (
    format_daily_calendar,
    format_macro_result,
    format_tw_result,
    format_us_result,
)

log = get_logger("ir.radar")

STATE_FILE = config.DATA_DIR / "radar_state.json"


# ── 推播去重 state ────────────────────────────────────────


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("radar_state.json 損毀，重建")
    return {"pushed": []}


def _save_state(state: dict) -> None:
    state["pushed"] = state["pushed"][-2000:]
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# ── 任務 ──────────────────────────────────────────────────


def run_calendar() -> None:
    log.info("===== 財報雷達行事曆開始 =====")
    today = datetime.now(config.TZ_TAIPEI).date()
    end = today + timedelta(days=(4 - today.weekday()) % 7 + 7)  # 下週五
    log.info("行事曆區間：%s ～ %s", today, end)

    tw_e = _safe(lambda: tw.fetch_calendar(today, end), "台股財報行事曆") or []
    us_e = _safe(lambda: us.fetch_calendar(today, end), "美股財報行事曆") or []
    macro_e = _safe(lambda: macro.fetch_calendar(today, end), "總經行事曆") or []

    log.info("行事曆筆數：台股 %d、美股 %d、總經 %d", len(tw_e), len(us_e), len(macro_e))
    push_text(format_daily_calendar(tw_e, us_e, macro_e, today))
    log.info("===== 財報雷達行事曆完成 =====")


def run_results() -> None:
    log.info("===== 財報雷達結果開始 =====")
    state = _load_state()
    pushed = set(state["pushed"])
    count = 0

    for label, fetcher, fmt, id_fn in (
        ("台股財報", tw.fetch_results, format_tw_result,
         lambda e: f"tw:{e.code}:{e.date}"),
        ("美股財報", us.fetch_results, format_us_result,
         lambda e: f"us:{e.symbol}:{e.date}"),
        ("總經數據", macro.fetch_results, format_macro_result,
         lambda e: f"macro:{e.source_id or e.name + str(e.dt_taipei)}"),
    ):
        for ev in _safe(fetcher, f"{label}結果") or []:
            eid = id_fn(ev)
            if eid in pushed:
                continue
            push_text(fmt(ev))
            pushed.add(eid)
            state["pushed"].append(eid)
            count += 1
            time.sleep(1)

    _save_state(state)
    log.info("===== 財報雷達結果完成，新推播 %d 則 =====", count)


def run_daemon() -> None:
    log.info("財報雷達常駐模式啟動（Ctrl+C 結束）")
    last_calendar_date = None
    while True:
        now = datetime.now(config.TZ_TAIPEI)
        if now.hour == 7 and last_calendar_date != now.date():
            _safe(run_calendar, "行事曆任務")
            last_calendar_date = now.date()
        _safe(run_results, "結果任務")
        time.sleep(15 * 60)


def _safe(fn, label: str):
    try:
        return fn()
    except Exception:
        log.exception("%s 執行失敗", label)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="財報雷達子系統")
    parser.add_argument("--test", action="store_true", help="立即執行一次完整測試")
    parser.add_argument("--calendar", action="store_true", help="只推播行事曆")
    parser.add_argument("--result", action="store_true", help="只抓最新結果")
    parser.add_argument("--daemon", action="store_true", help="常駐排程模式")
    args = parser.parse_args()

    if args.daemon:
        run_daemon()
    elif args.calendar:
        run_calendar()
    elif args.result:
        run_results()
    elif args.test:
        run_calendar()
        run_results()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
