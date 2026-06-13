"""財報雷達 - 共用資料模型。"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TwEarning:
    """台股財報事件。"""
    code: str                # 股票代號，如 2330
    name: str                # 公司名稱
    date: str                # YYYY-MM-DD（台灣時間）
    time: str = ""           # HH:MM，未知留空
    note: str = ""           # 來源備註（例：董事會決議、法說會）
    # 結果欄位（公布後填入）
    eps_actual: float | None = None
    eps_last_year: float | None = None      # 去年同期 EPS（無分析師預期時的比較基準）
    eps_estimate: float | None = None       # 市場預期（若有來源）
    revenue: float | None = None            # 營收（億元）
    revenue_yoy: float | None = None        # 年增率 %
    revenue_qoq: float | None = None        # 季增率 %
    eps_yoy: float | None = None            # EPS 年增率 %


@dataclass
class UsEarning:
    """美股財報事件。"""
    symbol: str
    name: str
    date: str                # YYYY-MM-DD（美東日期）
    session: str = ""        # BMO（盤前）/ AMC（盤後）/ 空字串未知
    eps_estimate: float | None = None
    # 結果欄位
    eps_actual: float | None = None
    surprise_pct: float | None = None       # EPS 超預期幅度 %
    revenue_actual: float | None = None     # 美元
    revenue_estimate: float | None = None
    revenue_yoy: float | None = None
    price_reaction_pct: float | None = None  # 盤後/盤前漲跌幅 %


@dataclass
class MacroEvent:
    """總經數據事件。"""
    country: str             # 🇺🇸 / 🇹🇼 等
    name: str                # 指標名稱（中文）
    dt_taipei: datetime      # 公布時間（台灣時區）
    previous: str = ""       # 前值（保留原始字串，含 % 或單位）
    forecast: str = ""       # 市場預期值
    actual: str = ""         # 實際值（公布後填入）
    lower_is_better: bool = False   # CPI/PPI/失業率等：低於預期為利多
    impact: int = 0          # 重要度 1-3
    source_id: str = ""      # 來源事件 ID（去重用）
    interpretation: str = field(default="", compare=False)  # 對市場影響解讀
