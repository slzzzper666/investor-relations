"""Gemini 共用工具：重試 + 多模型降級鏈。

免費版額度是「每模型每日」分開計算（gemini-2.5-flash 僅 20 次/日），
所以 429 額度耗盡時自動換下一個模型，全部串起來才夠一天的量。
涵蓋的暫時性錯誤：
- API 層：429/5xx（google.genai APIError）
- 傳輸層：httpx 連線中斷、讀取逾時（大音檔上傳時常見）
"""
import time

import httpx
from google.genai import errors

from ir.logger import get_logger

log = get_logger("ir.gemini")

# 都支援音檔／PDF 輸入與 structured output；品質由高到低。
# 各模型的免費額度分開計算，所以鏈越長、一天能處理的量越多。
# 2026-09 更新：2.0 系列已自 API 下架（呼叫回 404），換成 3.x 系列。
# 清單要定期對照 client.models.list() 檢查，模型下架不會有預告。
MODEL_CHAIN = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

_RETRYABLE_CODES = {429, 500, 502, 503, 504}
# 額度耗盡／已下架的模型記到這裡，當次執行後續呼叫直接跳過
_exhausted: set[str] = set()
# 連續回 5xx 的次數；批次作業時新模型常整段時間都在過載，
# 連錯 _DOWN_LIMIT 次就當它這輪不可用，免得每次呼叫都白等它逾時
_fail_streak: dict[str, int] = {}
_DOWN_LIMIT = 3


def all_exhausted() -> bool:
    """所有 Gemini 模型今日額度都耗盡時回 True（讓呼叫端直接走 Groq）。"""
    return all(m in _exhausted for m in MODEL_CHAIN)


def generate_with_retry(client, *, attempts: int = 3, **kwargs):
    """generate_content 包裝：掃過整條 MODEL_CHAIN，失敗就換下一個模型。

    策略是「橫向優先」——遇到限流或模型異常時立刻換下一個模型，而不是在同一個
    模型上等待重試。因為鏈上有多個獨立額度的模型，換模型（0 秒）幾乎一定比
    等待窗口重置（60 秒以上）更快拿到結果。整條鏈都失敗才退避、再掃一輪，
    最多掃 attempts 輪。

    呼叫端不需指定 model（會忽略傳入值，一律走 MODEL_CHAIN）。
    """
    kwargs.pop("model", None)
    last_exc: Exception | None = None

    for round_i in range(attempts):
        tried_any = False
        for model in MODEL_CHAIN:
            if model in _exhausted:
                continue
            tried_any = True
            try:
                resp = client.models.generate_content(model=model, **kwargs)
                _fail_streak.pop(model, None)
                return resp
            except errors.APIError as e:
                last_exc = e
                if e.code == 429 and "PerDay" in str(e):
                    # 每日額度，隔天才回復 → 當天不再試這個模型
                    _exhausted.add(model)
                    log.warning("%s 每日額度耗盡，換下一個模型", model)
                elif e.code == 404:
                    # 模型已下架（Google 不預告），當次執行不再試
                    _exhausted.add(model)
                    log.warning("%s 已不存在（404），換下一個模型", model)
                elif e.code in _RETRYABLE_CODES:
                    n = _fail_streak.get(model, 0) + 1
                    _fail_streak[model] = n
                    if n >= _DOWN_LIMIT:
                        _exhausted.add(model)
                        log.warning("%s 連續 %d 次回 %s，本次執行不再嘗試",
                                    model, n, e.code)
                    else:
                        log.warning("%s 回 %s，換下一個模型", model, e.code)
                else:
                    raise  # 4xx 邏輯錯誤（如 prompt 格式），換模型也沒用
            except (httpx.HTTPError, ConnectionError) as e:
                last_exc = e
                log.warning("%s 連線層錯誤 %s，換下一個模型",
                            model, type(e).__name__)

        if not tried_any:
            break  # 全鏈額度都耗盡，再掃也沒用
        if round_i < attempts - 1:
            wait = 30 * (round_i + 1)
            log.warning("整條模型鏈本輪皆失敗，%d 秒後重掃", wait)
            time.sleep(wait)

    raise RuntimeError(f"所有 Gemini 模型都失敗，最後錯誤：{last_exc}")
