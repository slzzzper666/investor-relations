"""法說會的「財報季」歸屬。

問題：同一季財報，一家公司常開好幾場法說會（自辦一場、券商協辦好幾場），
2026 年最多的一家同季開了 12 場。這些場次講的是同一份財報，兩兩比較幾乎一樣，
「與上次法說會比較」要有意義，就必須跨季比。

定義：一場法說會歸屬於「它召開時最近一個已結束的季」——
  法說會日期落在第 N 季 → 歸屬第 N−1 季。
  1～3 月 → 去年 Q4（年度）；4～6 月 → Q1；7～9 月 → Q2；10～12 月 → Q3。
這條規則同時涵蓋兩種常見節奏：季後隨即開（台積電 1/4/7/10 月）與
財報申報期限前後開（多數中小型 3/5/8/11 月），兩者都會落在同一個季標籤下。
邊界（如 9/30 與 10/1）本質上無法從日期判斷，改以「不同季且相隔夠久」兩個條件一起把關。
"""
from datetime import date, timedelta

MIN_GAP_DAYS = 21   # 「上一季那場」至少要早這麼多天（擋掉跨季邊界上只差幾天的兩場）


def season_of(d: "date | str") -> str:
    """法說會日期 → 財報季標籤，如 '2026Q2'。"""
    ds = d.isoformat() if isinstance(d, date) else str(d)
    y, m = int(ds[:4]), int(ds[5:7])
    q = (m - 1) // 3          # 本季序號 1~4 減 1 後的值（0=Q1）→ 直接就是上一季
    if q == 0:
        return f"{y - 1}Q4"
    return f"{y}Q{q}"


def season_label(s: str) -> str:
    """'2026Q2' → '2026 Q2'（顯示用）。"""
    return f"{s[:4]} {s[4:]}" if len(s) == 6 else s


def pick_previous(cur_date: "date | str", candidates: list) -> dict | None:
    """從候選場次（需含 'date'，日期新→舊）挑出可比的「上一季那場」。

    條件：財報季不同，且早於 cur_date 至少 MIN_GAP_DAYS 天。
    取符合條件中最新的一場——也就是上一季的最後一場，資訊最完整。
    """
    ds = cur_date.isoformat() if isinstance(cur_date, date) else str(cur_date)
    y, m, d = map(int, ds.split("-"))
    cutoff = (date(y, m, d) - timedelta(days=MIN_GAP_DAYS)).isoformat()
    cur_season = season_of(ds)
    for c in candidates:
        cd = c.get("date", "")
        if cd and cd <= cutoff and season_of(cd) != cur_season:
            return c
    return None
