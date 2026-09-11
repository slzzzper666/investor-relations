"""總經「每月回顧」建置：每個已結束的月份一段股／債／房回顧。

資料骨幹：yfinance 月度漲跌（同 build_macro 的追蹤標的）＋ 該月已公布的總經數據
（macro_history.json）。文字由 Gemini 依這些數字撰寫，嚴禁引用未提供的數據。

冪等：只補「已結束且尚未有回顧」的月份，掛進每日 CI 也只會在月初多做一次。
原 1～6 月回顧為人工撰寫，格式相同、原樣保留。

輸出：site/public/macro_monthly.json
用法：python site/build_macro_monthly.py            補缺的月份
      python site/build_macro_monthly.py --force 2026-08  重做指定月份
"""
import json
import sys
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass
warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
PUBLIC_DIR = BASE_DIR / "public"
OUT = PUBLIC_DIR / "macro_monthly.json"
HISTORY = PUBLIC_DIR / "macro_history.json"
sys.path.insert(0, str(ROOT_DIR))

from google.genai import types                      # noqa: E402
from pydantic import BaseModel                      # noqa: E402

import config                                       # noqa: E402
from config import TZ_TAIPEI                        # noqa: E402
from ir.gemini_util import generate_with_retry      # noqa: E402
from ir.logger import get_logger                    # noqa: E402

log = get_logger("build_macro_monthly")
FIRST_MONTH = "2026-01"

# (symbol, 中文名, 類型)  類型：pct=用漲跌%、bp=用基點變化（殖利率）
TRACK = {
    "equity": [("^GSPC", "標普500", "pct"), ("^IXIC", "那斯達克", "pct"),
               ("^TWII", "台股加權", "pct"), ("^VIX", "VIX", "pct")],
    "bond": [("^TNX", "美10年期殖利率", "bp"), ("^TYX", "美30年期殖利率", "bp"),
             ("TLT", "20年期以上公債ETF", "pct")],
    "realestate": [("XHB", "美房屋建商ETF", "pct"), ("ITB", "美住宅營建ETF", "pct"),
                   ("IYR", "美REIT ETF", "pct")],
    "commodity": [("GC=F", "黃金", "pct"), ("CL=F", "西德州原油", "pct"),
                  ("DX-Y.NYB", "美元指數", "pct")],
}


class _Monthly(BaseModel):
    summary: str
    equity: str
    bond: str
    realestate: str


_SYSTEM = (
    "你是資深總體經濟與資產配置分析師，撰寫月度市場回顧。全部使用繁體中文（台灣用語與"
    "金融術語），專業、客觀、精簡，不用 emoji。極重要：只能引用使用者提供的數字與事件，"
    "嚴禁杜撰任何未提供的數據、事件或機構預測；沒給的就不要寫。"
)

_PROMPT = """請撰寫 {label} 的市場回顧。以下是該月的實際數據（只能依據這些，不得自行補充）：

【月度漲跌（月初→月底收盤）】
{moves}

【該月公布的總經數據】
{events}

輸出 JSON（不要 markdown 圍欄），四個欄位皆為純文字段落：
{{
 "summary": "一句話總結該月股／債／房格局（25 字內）",
 "equity": "股市回顧：對照標普500、那斯達克、台股加權、VIX 的漲跌，並連結該月公布的就業／通膨數據解釋原因（3～5 句）",
 "bond": "債市回顧：對照各天期殖利率變化（以基點表示）與長債 ETF 漲跌，連結通膨數據與政策預期（2～4 句）",
 "realestate": "房市回顧：對照房屋建商／營建／REIT ETF 表現，並連結長端殖利率對房貸利率的意涵（2～3 句）"
}}

寫法參考（語氣與密度）：
「標普500下跌0.6%、那斯達克下跌1.5%、台股加權回落2.6%，高檔獲利了結拉回整理。基本面尚穩：5月非農17.2萬人優於預期…」"""


def _month_bounds(ym: str) -> tuple[date, date]:
    y, m = int(ym[:4]), int(ym[5:7])
    first = date(y, m, 1)
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return first, nxt - timedelta(days=1)


def _monthly_moves(ym: str) -> list[str]:
    """該月各標的漲跌：用上月最後收盤 → 本月最後收盤。"""
    import yfinance as yf

    first, last = _month_bounds(ym)
    lines = []
    for group, items in TRACK.items():
        for sym, name, kind in items:
            try:
                h = yf.Ticker(sym).history(start=(first - timedelta(days=10)).isoformat(),
                                           end=(last + timedelta(days=1)).isoformat(),
                                           auto_adjust=False)
            except Exception as e:  # noqa: BLE001
                log.warning("%s 抓取失敗：%s", sym, e)
                continue
            if h is None or h.empty:
                continue
            closes = h["Close"].dropna()
            before = closes[closes.index.date < first]
            within = closes[(closes.index.date >= first) & (closes.index.date <= last)]
            if before.empty or within.empty:
                continue
            ref, end = float(before.iloc[-1]), float(within.iloc[-1])
            if kind == "bp":
                lines.append(f"- {name}：{ref:.2f}% → {end:.2f}%（{(end - ref) * 100:+.0f} 個基點）")
            else:
                lines.append(f"- {name}：{ref:,.2f} → {end:,.2f}（{(end / ref - 1) * 100:+.1f}%）")
    return lines


def _month_events(ym: str) -> list[str]:
    if not HISTORY.exists():
        return []
    items = json.loads(HISTORY.read_text(encoding="utf-8")).get("items", [])
    out = []
    for it in items:
        if it["date"][:7] != ym or not it.get("actual"):
            continue
        bits = [f"實際 {it['actual']}"]
        if it.get("forecast"):
            bits.append(f"預期 {it['forecast']}")
        if it.get("previous"):
            bits.append(f"前值 {it['previous']}")
        out.append(f"- {it['date'][5:]} {it['country']} {it['name']}：{'、'.join(bits)}")
    return out


def build_month(ym: str) -> dict:
    moves = _monthly_moves(ym)
    events = _month_events(ym)
    if not moves:
        raise RuntimeError(f"{ym} 抓不到任何月度漲跌")
    label = f"{ym[:4]} 年 {int(ym[5:7])} 月"
    prompt = _PROMPT.format(label=label, moves="\n".join(moves),
                            events="\n".join(events) or "（該月無收錄的總經數據）")
    resp = generate_with_retry(
        [prompt],
        timeout_ms=120_000,
        config_=types.GenerateContentConfig(
            temperature=0.3,
            system_instruction=_SYSTEM,
            response_mime_type="application/json",
            response_schema=_Monthly,
        ),
    )
    parsed = resp.parsed
    if not isinstance(parsed, _Monthly):
        parsed = _Monthly.model_validate(json.loads(resp.text))
    log.info("%s 回顧已生成（%s）", ym, getattr(resp, "model_version", ""))
    return {"month": ym, **parsed.model_dump()}


def main() -> None:
    if not config.GEMINI_API_KEYS:
        raise SystemExit("缺少 GEMINI_API_KEY")
    force = None
    if "--force" in sys.argv:
        force = sys.argv[sys.argv.index("--force") + 1]

    data = {"generated_at": "", "months": []}
    if OUT.exists():
        try:
            data = json.loads(OUT.read_text(encoding="utf-8"))
        except ValueError:
            pass
    have = {m["month"]: m for m in data.get("months", [])}

    today = datetime.now(TZ_TAIPEI).date()
    # 已結束的月份：上個月（含）以前
    last_done = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    wanted = []
    y, m = int(FIRST_MONTH[:4]), int(FIRST_MONTH[5:7])
    while f"{y}-{m:02d}" <= last_done:
        wanted.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    todo = [ym for ym in wanted if ym not in have or ym == force]
    print(f"===== 每月回顧：既有 {len(have)} 個月，待補 {todo or '無'} =====")

    for ym in todo:
        try:
            have[ym] = build_month(ym)
            print(f"  {ym}：{have[ym]['summary']}")
        except Exception as e:  # noqa: BLE001
            log.warning("%s 生成失敗：%s", ym, str(e)[:160])

    months = [have[k] for k in sorted(have)]
    OUT.write_text(json.dumps({
        "generated_at": today.isoformat(), "months": months,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"已寫入 {OUT}：{len(months)} 個月（{[m['month'] for m in months]}）")


if __name__ == "__main__":
    main()
