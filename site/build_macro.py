"""總經【未來】頁資料建置：股市／債市／房市／商品匯率儀表板。

資料骨幹用 yfinance（雲端可跑、免金鑰、有歷史可畫縮圖），輸出每檔指標的
最新值、日／月／今年漲跌與縮圖序列。經濟數據（CPI／失業率／房貸利率…）以
FRED 免金鑰 CSV 端點「防禦式」附加——抓到才顯示，抓不到不影響整頁。
AI 分析與「股市預期」由 Claude 另行撰寫於 data/macro_ai.json，建置時併入。

輸出：
  site/public/macro_future.json   未來頁全部內容（資料 + AI）

用法：
  .venv\\Scripts\\python site\\build_macro.py
"""
import json
import sys
import warnings
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

try:  # Windows 主控台預設 cp950，印中文標點會炸；CI(Linux) 本就 utf-8
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

warnings.filterwarnings("ignore")
import requests          # noqa: E402
import yfinance as yf    # noqa: E402

BASE_DIR = Path(__file__).resolve().parent      # site/
ROOT_DIR = BASE_DIR.parent                       # 專案根
PUBLIC_DIR = BASE_DIR / "public"
# AI 分析的「基準版」（Claude 手寫）——Gemini 自動生成失敗時的安全網。
# 放 site/ 隨 repo 提交，CI 才讀得到。
AI_FILE = BASE_DIR / "macro_ai.json"

sys.path.insert(0, str(ROOT_DIR))   # 讓 build_macro 能 import ir.*

TAIPEI = timezone(timedelta(hours=8))

# (group_key, 中文標題, [(symbol, 中文名, kind)])
# kind：point=指數點數、rate=殖利率(%)、usd=美元計價
GROUPS = [
    ("equity", "股市", [
        ("^GSPC", "標普 500 指數", "point"),
        ("^IXIC", "那斯達克指數", "point"),
        ("^TWII", "台股加權指數", "point"),
        ("^VIX", "VIX 波動率指數", "point"),
    ]),
    ("bond", "債市", [
        ("^TNX", "美國 10 年期公債殖利率", "rate"),
        ("^TYX", "美國 30 年期公債殖利率", "rate"),
        ("^IRX", "美國 13 週國庫券利率", "rate"),
        ("TLT", "20 年期以上公債 ETF", "usd"),
    ]),
    ("realestate", "房市", [
        ("XHB", "美國房屋建商 ETF", "usd"),
        ("ITB", "美國住宅營建 ETF", "usd"),
        ("IYR", "美國房地產 REIT ETF", "usd"),
    ]),
    ("commodity", "商品 ・ 匯率", [
        ("GC=F", "黃金期貨", "usd"),
        ("CL=F", "西德州原油", "usd"),
        ("DX-Y.NYB", "美元指數", "point"),
    ]),
]

SPARK_POINTS = 52   # 縮圖取樣點數（約一年週線）


def _chip(label: str, ref, latest, kind: str) -> dict | None:
    """組一個漲跌標籤：價格類用 %、殖利率類用基點(bp)。"""
    if ref is None or latest is None or ref == 0:
        return None
    if kind == "rate":
        bp = round((latest - ref) * 100)
        if bp == 0:
            return {"label": label, "text": "0bp", "dir": 0}
        return {"label": label, "text": f"{'+' if bp > 0 else ''}{bp}bp",
                "dir": 1 if bp > 0 else -1}
    pct = (latest - ref) / abs(ref) * 100
    return {"label": label, "text": f"{'+' if pct >= 0 else ''}{pct:.1f}%",
            "dir": 1 if pct >= 0 else -1}


def _downsample(values: list[float], n: int) -> list[float]:
    if len(values) <= n:
        return [round(v, 4) for v in values]
    step = len(values) / n
    out = [values[min(len(values) - 1, int(i * step))] for i in range(n)]
    out[-1] = values[-1]   # 末點務必為最新
    return [round(v, 4) for v in out]


def _fmt_latest(v: float, kind: str) -> str:
    if kind == "rate":
        return f"{v:.2f}"
    if v >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def fetch_item(symbol: str, name: str, kind: str) -> dict | None:
    try:
        hist = yf.Ticker(symbol).history(period="1y", auto_adjust=False)
    except Exception as e:  # noqa: BLE001
        print(f"  {symbol} 抓取失敗：{e}")
        return None
    close = hist["Close"].dropna()
    if len(close) < 20:
        print(f"  {symbol} 資料不足（{len(close)} 點）")
        return None

    vals = [float(x) for x in close.values]
    idx = close.index
    latest = vals[-1]
    asof = idx[-1].strftime("%Y-%m-%d")

    prev = vals[-2] if len(vals) >= 2 else None
    m1 = vals[-22] if len(vals) >= 22 else vals[0]
    # 今年以來：今年第一個交易日
    this_year = idx[-1].year
    ytd_ref = None
    for i, ts in enumerate(idx):
        if ts.year == this_year:
            ytd_ref = vals[i]
            break
    if ytd_ref is None:
        ytd_ref = vals[0]

    deltas = [c for c in (
        _chip("日", prev, latest, kind),
        _chip("月", m1, latest, kind),
        _chip("今年", ytd_ref, latest, kind),
    ) if c]

    unit = {"rate": "%", "usd": "美元", "point": ""}[kind]
    return {
        "symbol": symbol, "name": name, "kind": kind, "unit": unit,
        "value": _fmt_latest(latest, kind), "raw": round(latest, 4),
        "asof": asof, "deltas": deltas,
        "spark": _downsample(vals, SPARK_POINTS),
    }


# ── 經濟數據（FRED 免金鑰 CSV，防禦式：抓不到就跳過） ──────────

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
# (series_id, 中文名, 模式)  模式：level=取最新值、yoy=年增率%
FRED_SERIES = [
    ("CPIAUCSL", "美國 CPI 年增率", "yoy", "%"),
    ("CPILFESL", "美國核心 CPI 年增率", "yoy", "%"),
    ("UNRATE", "美國失業率", "level", "%"),
    ("FEDFUNDS", "聯邦基金有效利率", "level", "%"),
    ("MORTGAGE30US", "美國 30 年期房貸利率", "level", "%"),
    ("T10Y2Y", "美債 10年-2年 利差", "level", "%"),
]


def _fred_series(sid: str):
    """回 [(date, value)]（已去除缺漏 '.'），失敗回 None。"""
    try:
        r = requests.get(FRED_CSV, params={"id": sid}, timeout=8,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except Exception:  # noqa: BLE001
        return None
    rows = []
    for line in StringIO(r.text).read().splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2 or parts[1] in ("", "."):
            continue
        try:
            rows.append((parts[0], float(parts[1])))
        except ValueError:
            continue
    return rows or None


def fetch_econ() -> list[dict]:
    out = []
    for sid, name, mode, unit in FRED_SERIES:
        rows = _fred_series(sid)
        if not rows:
            print(f"  FRED {sid} 無資料（跳過）")
            continue
        date, val = rows[-1]
        if mode == "yoy":
            prior = next((v for d, v in reversed(rows[:-1])
                          if d[:7] <= _months_ago(date, 12)), None)
            if prior is None or prior == 0:
                continue
            value = round((val - prior) / abs(prior) * 100, 1)
        else:
            value = round(val, 2)
        out.append({"name": name, "value": value, "unit": unit, "asof": date})
        print(f"  FRED {sid}：{value}{unit}（{date}）")
    return out


def _months_ago(date_str: str, months: int) -> str:
    y, m = int(date_str[:4]), int(date_str[5:7])
    m -= months
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def fetch_macro_calendar() -> list[dict]:
    """即將公布的總經數據（與 radar 推 TG/DC 同源：Investing.com）。

    Investing.com 擋雲端資料中心 IP → 雲端會抓到 0；由 main 的保護機制保留
    本機（家用 IP）建好並提交的 macro_upcoming.json。
    """
    from datetime import date, timedelta
    try:
        from ir.radar import macro as radar_macro
        evs = radar_macro.fetch_calendar(date.today(),
                                         date.today() + timedelta(days=75))
    except Exception as e:  # noqa: BLE001
        print(f"  總經行事曆抓取失敗（{type(e).__name__}: {e}）")
        return []
    items = []
    for e in evs:
        items.append({
            "date": e.dt_taipei.strftime("%Y-%m-%d"),
            "time": e.dt_taipei.strftime("%H:%M"),
            "country": e.country,
            "name": e.name,
            "previous": e.previous or "",
            "forecast": e.forecast or "",
            "impact": e.impact,
        })
    items.sort(key=lambda x: (x["date"], x["time"]))
    return items


def load_ai() -> dict:
    """讀手寫基準版 macro_ai.json（Gemini 失敗時的安全網）。"""
    if AI_FILE.exists():
        try:
            return json.loads(AI_FILE.read_text(encoding="utf-8"))
        except ValueError:
            print("macro_ai.json 解析失敗，AI 區塊留空")
    return {}


def build_ai(groups: list[dict], econ: list[dict]) -> dict:
    """優先用 Gemini 依即時數字自動生成；失敗則退回手寫基準版。"""
    try:
        from ir.macro_ai import generate_macro_ai
        ai = generate_macro_ai(groups, econ)
        print("  AI：Gemini 自動生成成功")
        return ai
    except Exception as e:  # noqa: BLE001
        print(f"  AI：Gemini 生成失敗（{type(e).__name__}: {e}），改用手寫基準版")
        return load_ai()


def main() -> None:
    print("===== 總經未來頁建置 =====")
    groups = []
    for key, label, syms in GROUPS:
        print(f"[{label}]")
        items = [it for s, n, k in syms if (it := fetch_item(s, n, k))]
        if items:
            groups.append({"key": key, "label": label, "items": items})

    print("[經濟數據 FRED]")
    econ = fetch_econ()

    generated_at = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
    n_items = sum(len(g["items"]) for g in groups)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    out = PUBLIC_DIR / "macro_future.json"

    # 保護：yfinance 當日被限流、抓到的指標過少時，不以殘缺資料覆蓋既有好版本
    # （也避免拿殘缺數字去餵 AI 生成誤導性分析）
    if n_items < 6 and out.exists():
        print(f"⚠ 僅抓到 {n_items} 指標（疑似來源限流），保留既有 macro_future.json 不覆蓋")
        return

    print("[AI 分析]")
    ai = build_ai(groups, econ)

    payload = {
        "generated_at": generated_at,
        "ai_asof": ai.get("asof", ""),
        "groups": groups,
        "econ": econ,
        "ai": ai.get("ai", {}),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"已寫入 {out}（{out.stat().st_size:,} bytes）："
          f"{len(groups)} 類 {n_items} 指標、經濟數據 {len(econ)} 筆、"
          f"AI {'有' if ai.get('ai') else '無'}")

    # 總經行事曆（即將公布的數據；Investing.com 擋雲端 → 抓到 0 就保留既有）
    print("[總經行事曆]")
    cal = fetch_macro_calendar()
    cal_path = PUBLIC_DIR / "macro_upcoming.json"
    prev_count = 0
    if cal_path.exists():
        try:
            prev_count = json.loads(
                cal_path.read_text(encoding="utf-8")).get("count", 0)
        except (ValueError, OSError):
            prev_count = 0
    if not cal and prev_count > 0:
        print(f"  抓到 0 筆（疑似 Investing 擋雲端 IP），保留既有 {prev_count} 筆")
    else:
        cal_path.write_text(
            json.dumps({"generated_at": generated_at, "count": len(cal),
                        "items": cal}, ensure_ascii=False), encoding="utf-8")
        print(f"  總經行事曆：{len(cal)} 筆")


if __name__ == "__main__":
    main()
