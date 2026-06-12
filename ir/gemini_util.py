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

# 都支援音檔輸入與 structured output；品質由高到低
MODEL_CHAIN = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

_RETRYABLE_CODES = {429, 500, 502, 503, 504}
# 額度耗盡的模型記到這裡，當天後續呼叫直接跳過
_exhausted: set[str] = set()


def generate_with_retry(client, *, attempts: int = 3, **kwargs):
    """generate_content 包裝：每個模型最多重試 attempts 次，429 即換下一個模型。

    呼叫端不需指定 model（會忽略傳入值，一律走 MODEL_CHAIN）。
    """
    kwargs.pop("model", None)
    last_exc: Exception | None = None

    for model in MODEL_CHAIN:
        if model in _exhausted:
            continue
        for i in range(attempts):
            try:
                return client.models.generate_content(model=model, **kwargs)
            except errors.APIError as e:
                last_exc = e
                if e.code == 429:
                    msg = str(e)
                    if "PerDay" in msg:
                        # 每日額度，隔天才回復 → 當天直接換模型
                        _exhausted.add(model)
                        log.warning("%s 每日額度耗盡，換下一個模型", model)
                        break
                    if i == attempts - 1:
                        break  # 同模型重試夠多次了，換下一個
                    # 每分鐘限流：等窗口重置再試同一個模型
                    wait = 65
                    log.warning("%s 限流（RPM），%d 秒後重試", model, wait)
                    time.sleep(wait)
                    continue
                if e.code not in _RETRYABLE_CODES or i == attempts - 1:
                    if e.code in _RETRYABLE_CODES:
                        break  # 這個模型壞了，試下一個
                    raise  # 4xx 邏輯錯誤，換模型也沒用
                wait = 20 * (2 ** i)
                log.warning("%s 回 %s（第 %d 次），%d 秒後重試",
                            model, e.code, i + 1, wait)
                time.sleep(wait)
            except (httpx.HTTPError, ConnectionError) as e:
                last_exc = e
                if i == attempts - 1:
                    break
                wait = 30 * (i + 1)
                log.warning("%s 連線層錯誤 %s（第 %d 次），%d 秒後重試",
                            model, type(e).__name__, i + 1, wait)
                time.sleep(wait)

    raise RuntimeError(f"所有 Gemini 模型都失敗，最後錯誤：{last_exc}")
