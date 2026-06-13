"""財報雷達 - 推播訊息格式化（依規格書版面）。"""
from datetime import date, datetime, timedelta

from config import TZ_TAIPEI
from ir.radar.models import MacroEvent, TwEarning, UsEarning

LINE = "━━━━━━━━━━━━━━━━━━"
WEEKDAYS = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]


def _day_label(d: date, today: date) -> str:
    if d == today:
        return "今日"
    if d == today + timedelta(days=1):
        return "明日"
    return f"{WEEKDAYS[d.weekday()]} {d.month}/{d.day}"


def _num(v: float | None, fmt: str = "{:.2f}") -> str:
    return fmt.format(v) if v is not None else "—"


def _pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.1f}%"


# ── 每日早上行事曆 ────────────────────────────────────────


def format_daily_calendar(
    tw: list[TwEarning],
    us: list[UsEarning],
    macro: list[MacroEvent],
    today: date | None = None,
) -> str:
    today = today or datetime.now(TZ_TAIPEI).date()
    out = [
        f"📅 財報雷達｜{today:%Y/%m/%d} 行事曆",
        "今日至下週五重要事件",
        LINE,
    ]

    # 台股財報（依日期分組）
    out.append("🇹🇼 台股財報")
    if tw:
        by_day: dict[str, list[TwEarning]] = {}
        for e in sorted(tw, key=lambda x: (x.date, x.time or "99:99", x.code)):
            by_day.setdefault(e.date, []).append(e)
        for d_str, items in by_day.items():
            d = date.fromisoformat(d_str)
            out.append(f"📌 {_day_label(d, today)}")
            for e in items:
                if e.note:
                    t = f" - {e.note} {e.time}".rstrip()
                elif e.time:
                    t = f" - 預計 {e.time} 公布"
                else:
                    t = ""
                out.append(f"・{e.code} {e.name}{t}")
    else:
        out.append("（期間內無已知台股財報事件）")

    out.append(LINE)

    # 美股財報（依日期 + 盤前/盤後分組）
    out.append("🇺🇸 美股財報")
    if us:
        session_label = {"BMO": "盤前", "AMC": "盤後", "": ""}
        session_order = {"BMO": 0, "AMC": 1, "": 2}
        by_key: dict[tuple[str, str], list[UsEarning]] = {}
        for e in sorted(
            us, key=lambda x: (x.date, session_order.get(x.session, 2), x.symbol)
        ):
            by_key.setdefault((e.date, e.session), []).append(e)
        for (d_str, sess), items in by_key.items():
            d = date.fromisoformat(d_str)
            out.append(f"📌 {_day_label(d, today)}{session_label.get(sess, sess)}")
            for e in items:
                est = f" - EPS預期 ${e.eps_estimate:.2f}" if e.eps_estimate is not None else ""
                out.append(f"・{e.symbol} {e.name}{est}")
    else:
        out.append("（期間內無重要美股財報）")

    out.append(LINE)

    # 總經數據
    out.append("📊 總經數據")
    if macro:
        today_items = [m for m in macro if m.dt_taipei.date() == today]
        later_items = [m for m in macro if m.dt_taipei.date() > today]
        if today_items:
            out.append("📌 今日")
            for m in sorted(today_items, key=lambda x: x.dt_taipei):
                extra = _prev_forecast(m)
                out.append(f"・{m.dt_taipei:%H:%M} {m.country} {m.name}{extra}")
        if later_items:
            out.append("📌 本週/下週")
            for m in sorted(later_items, key=lambda x: x.dt_taipei):
                d = m.dt_taipei
                extra = _prev_forecast(m)
                out.append(
                    f"・{_day_label(d.date(), today)} {d:%H:%M} {m.country} {m.name}{extra}"
                )
    else:
        out.append("（期間內無重要總經數據）")

    out.append(LINE)
    return "\n".join(out)


def _prev_forecast(m: MacroEvent) -> str:
    bits = []
    if m.previous:
        bits.append(f"前值 {m.previous}")
    if m.forecast:
        bits.append(f"預期 {m.forecast}")
    return f"（{'，'.join(bits)}）" if bits else ""


# ── 財報結果：台股 ────────────────────────────────────────


def _mark(actual: float | None, baseline: float | None, tolerance: float = 0.02):
    """回傳 (emoji, 說明)。baseline 容忍 ±2% 視為符合。"""
    if actual is None or baseline is None:
        return "➡️", "無比較基準"
    if baseline == 0:
        diff = actual - baseline
        return ("✅", "超預期") if diff > 0 else ("❌", "低於預期") if diff < 0 else ("➡️", "符合預期")
    ratio = (actual - baseline) / abs(baseline)
    if ratio > tolerance:
        return "✅", f"超預期 +{ratio * 100:.1f}%"
    if ratio < -tolerance:
        return "❌", f"低於預期 {ratio * 100:.1f}%"
    return "➡️", "符合預期"


def format_tw_result(e: TwEarning) -> str:
    baseline = e.eps_estimate if e.eps_estimate is not None else e.eps_last_year
    base_label = "預期" if e.eps_estimate is not None else "去年同期"
    emoji, desc = _mark(e.eps_actual, baseline)
    if e.eps_estimate is None:  # 基準是去年同期時改寫措辭
        desc = (desc.replace("超預期", "優於去年同期")
                    .replace("低於預期", "低於去年同期")
                    .replace("符合預期", "與去年同期相當"))
    out = [
        f"🔔 財報結果｜{e.code} {e.name}",
        f"{emoji} EPS {desc}" if baseline is not None else "➡️ EPS（無比較基準）",
        f"實際：{_num(e.eps_actual)}｜{base_label}：{_num(baseline)}",
    ]
    if e.eps_yoy is not None:
        out.append(f"EPS 年增：{_pct(e.eps_yoy)}")
    if e.revenue is not None:
        rev_line = f"營收：{e.revenue:,.1f}億"
        growth = []
        if e.revenue_yoy is not None:
            growth.append(f"年增 {_pct(e.revenue_yoy)}")
        if e.revenue_qoq is not None:
            growth.append(f"季增 {_pct(e.revenue_qoq)}")
        if growth:
            rev_line += f"（{'，'.join(growth)}）"
        out.append(rev_line)
    out.append(LINE)
    return "\n".join(out)


# ── 財報結果：美股 ────────────────────────────────────────


def format_us_result(e: UsEarning) -> str:
    emoji, desc = _mark(e.eps_actual, e.eps_estimate)
    if e.surprise_pct is not None:  # 來源直接給 surprise 就用它
        if e.surprise_pct > 2:
            emoji, desc = "✅", f"超預期 +{e.surprise_pct:.1f}%"
        elif e.surprise_pct < -2:
            emoji, desc = "❌", f"低於預期 {e.surprise_pct:.1f}%"
        else:
            emoji, desc = "➡️", "符合預期"
    out = [
        f"🔔 財報結果｜{e.symbol} {e.name}",
        f"{emoji} EPS {desc}",
        f"實際：${_num(e.eps_actual)}｜預期：${_num(e.eps_estimate)}",
    ]
    if e.revenue_actual is not None:
        rev = f"營收：{_usd(e.revenue_actual)}"
        if e.revenue_yoy is not None:
            rev += f"（年增 {_pct(e.revenue_yoy)}）"
        out.append(rev)
        if e.revenue_estimate is not None:
            out.append(f"市場預期：{_usd(e.revenue_estimate)}")
    if e.price_reaction_pct is not None:
        arrow = "📈" if e.price_reaction_pct >= 0 else "📉"
        out.append(f"{arrow} 盤後反應：{_pct(e.price_reaction_pct)}")
    out.append(LINE)
    return "\n".join(out)


def _usd(v: float) -> str:
    if abs(v) >= 1e9:
        return f"${v / 1e9:,.2f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:,.1f}M"
    return f"${v:,.0f}"


# ── 總經數據結果 ──────────────────────────────────────────


def format_macro_result(m: MacroEvent) -> str:
    emoji, judgement = _judge_macro(m)
    out = [
        f"🔔 總經數據｜{m.country} {m.name}",
        f"{emoji} {judgement}",
        f"實際：{m.actual or '—'}",
        f"預期：{m.forecast or '—'}",
        f"前值：{m.previous or '—'}",
    ]
    if m.interpretation:
        out.append(f"💬 解讀：{m.interpretation}")
    out.append(LINE)
    return "\n".join(out)


def _parse_val(s: str) -> float | None:
    """把 '3.2%'、'250K'、'-1.5M' 之類字串轉成數字。"""
    if not s:
        return None
    t = s.replace(",", "").replace("%", "").strip()
    mult = 1.0
    if t and t[-1] in "KMBT":
        mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[t[-1]]
        t = t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return None


def _judge_macro(m: MacroEvent):
    a, f = _parse_val(m.actual), _parse_val(m.forecast)
    # 無預期值（台灣 CPI/外銷訂單等）退而與前值比較
    if f is None:
        p = _parse_val(m.previous)
        if a is None or p is None:
            return "➡️", "已公布（無預期值可比較）"
        if a == p:
            return "➡️", "與前值持平"
        better = (a < p) if m.lower_is_better else (a > p)
        return ("✅", "優於前值") if better else ("❌", "差於前值")
    if a is None:
        return "➡️", "已公布（無預期值可比較）"
    if a == f:
        return "➡️", "符合預期"
    better = (a < f) if m.lower_is_better else (a > f)
    return ("✅", "優於預期") if better else ("❌", "差於預期")
