"""產業分類與同業本益比。

分類採 MOPS 官方產業別代碼（上市 TWSE OpenAPI、上櫃 TPEx OpenAPI 共用同一套
代碼），全市場 100% 覆蓋，因此可以當成「族群篩選」的骨幹；業務項目 AI 標籤
（ir.business）是更細的第二層。

同業本益比取「中位數」而非平均：PE 分布右尾很長（虧損轉盈、題材股動輒破百），
平均會被少數極端值拉高，中位數才是能拿來抓合理估值的基準。
"""
import re

import requests

from ir.logger import get_logger

log = get_logger("ir.industry")

TWSE_INFO_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_INFO_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"

# MOPS 產業別代碼（已用實際成分股逐碼核對）
INDUSTRY_NAMES = {
    "01": "水泥", "02": "食品", "03": "塑膠", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙",
    "10": "鋼鐵", "11": "橡膠", "12": "汽車", "14": "建材營造",
    "15": "航運", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨",
    "20": "其他", "21": "化學", "22": "生技醫療", "23": "油電燃氣",
    "24": "半導體", "25": "電腦及週邊設備", "26": "光電", "27": "通信網路",
    "28": "電子零組件", "29": "電子通路", "30": "資訊服務", "31": "其他電子",
    "32": "文化創意", "33": "農業科技", "35": "綠能環保", "36": "數位雲端",
    "37": "運動休閒", "38": "居家生活", "91": "存託憑證",
}


def industry_name(code: str) -> str:
    return INDUSTRY_NAMES.get(code or "", "")


def fetch_industry_map(session: requests.Session | None = None) -> dict[str, str]:
    """回 {股票代號: 產業別代碼}（上市＋上櫃）。"""
    s = session or requests.Session()
    s.headers.setdefault("User-Agent", "Mozilla/5.0")
    out: dict[str, str] = {}

    def grab(url: str, code_key: str, ind_key: str, desc: str) -> int:
        try:
            try:
                rows = s.get(url, timeout=60).json()
            except requests.exceptions.SSLError:
                rows = s.get(url, timeout=60, verify=False).json()  # TPEx 憑證偶發
        except Exception as exc:  # noqa: BLE001
            log.warning("產業別來源 %s 失敗：%s", desc, exc)
            return 0
        n = 0
        for row in rows:
            code = str(row.get(code_key, "")).strip()
            ind = str(row.get(ind_key, "")).strip()
            if re.fullmatch(r"\d{4}", code) and ind in INDUSTRY_NAMES:
                out[code] = ind
                n += 1
        log.info("產業別來源 %s：%d 檔", desc, n)
        return n

    grab(TWSE_INFO_URL, "公司代號", "產業別", "上市")
    grab(TPEX_INFO_URL, "SecuritiesCompanyCode", "SecuritiesIndustryCode", "上櫃")
    log.info("產業別表：%d 檔", len(out))
    return out


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else round((xs[mid - 1] + xs[mid]) / 2, 2)


def peer_pe_stats(pe_by_code: dict[str, float],
                  industry_by_code: dict[str, str],
                  min_samples: int = 5) -> dict[str, dict]:
    """依產業彙總本益比 → {產業代碼: {median, mean, n}}。

    樣本數少於 min_samples 的產業不出數字（不具代表性，寧可不顯示）。
    """
    buckets: dict[str, list[float]] = {}
    for code, pe in pe_by_code.items():
        ind = industry_by_code.get(code)
        if ind and pe > 0:
            buckets.setdefault(ind, []).append(pe)

    stats = {}
    for ind, xs in buckets.items():
        if len(xs) < min_samples:
            continue
        stats[ind] = {
            "median": _median(xs),
            "mean": round(sum(xs) / len(xs), 2),
            "n": len(xs),
        }
    log.info("同業本益比：%d 個產業有足夠樣本", len(stats))
    return stats
