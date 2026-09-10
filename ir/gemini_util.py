"""Gemini 共用工具：多金鑰 × 多模型的自動輪替。

免費額度是「每金鑰、每模型、每日」分開計算，所以可用量 = 金鑰數 × 模型數。
掛兩組不同 Google 帳號的金鑰，一天能處理的量就翻倍。

輪替策略是「橫向優先」——遇到限流或模型異常時立刻換下一組（金鑰, 模型），
而不是在同一組上等待重試。因為換一組是 0 秒，等待限流窗口重置要 60 秒以上，
只要還有沒試過的組合，換過去幾乎一定比等待快。整輪都失敗才退避、再掃一輪。

耗盡狀態記在 (金鑰序號, 模型) 上：A 帳號的 flash 用完不代表 B 帳號也用完。
注意 Google 的免費額度以**美西時間**換日（台北 15:00 重置），不是台北午夜。
"""
import time

import httpx
from google import genai
from google.genai import errors

import config
from ir.logger import get_logger

log = get_logger("ir.gemini")

# 都支援音檔／PDF 輸入與 structured output；品質由高到低。
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
# 額度耗盡／已下架的 (金鑰序號, 模型)，當次執行後續呼叫直接跳過
_exhausted: set[tuple[int, str]] = set()
# 連續回 5xx 的次數；批次作業時新模型常整段時間都在過載，
# 連錯 _DOWN_LIMIT 次就當它這輪不可用，免得每次呼叫都白等它逾時
_fail_streak: dict[tuple[int, str], int] = {}
_DOWN_LIMIT = 3

_clients: dict[tuple[int, int], genai.Client] = {}


def _keys() -> list[str]:
    keys = getattr(config, "GEMINI_API_KEYS", None) or []
    return keys or ([config.GEMINI_API_KEY] if config.GEMINI_API_KEY else [])


def _client(key_idx: int, timeout_ms: int) -> genai.Client:
    """取得（金鑰, 逾時）對應的 client；同組合共用，不重複建立。"""
    ck = (key_idx, timeout_ms)
    if ck not in _clients:
        from google.genai import types
        _clients[ck] = genai.Client(
            api_key=_keys()[key_idx],
            http_options=types.HttpOptions(timeout=timeout_ms),
        )
    return _clients[ck]


def all_exhausted() -> bool:
    """所有金鑰的所有模型今日額度都耗盡時回 True（讓呼叫端直接走 Groq）。"""
    keys = _keys()
    if not keys:
        return True
    return all((i, m) in _exhausted
               for i in range(len(keys)) for m in MODEL_CHAIN)


def quota_status() -> str:
    """人看的額度概況，供批次作業印在 log 裡。"""
    keys = _keys()
    total = len(keys) * len(MODEL_CHAIN)
    left = total - len(_exhausted)
    return f"{len(keys)} 組金鑰 × {len(MODEL_CHAIN)} 個模型＝{total} 組，剩 {left} 組可用"


def generate_with_retry(contents, *, config_=None, attempts: int = 2,
                        timeout_ms: int = 600_000, **kwargs):
    """掃過所有（金鑰 × 模型）組合產生內容，失敗就換下一組。

    contents 可以是內容本身，也可以是 callable(client) -> contents。
    後者供「要先用該 client 上傳檔案」的情境（如音檔走 Files API，
    上傳的檔案綁定該金鑰，換金鑰就得重新上傳）；每組金鑰只解析一次。
    PDF 之類 20MB 以內的檔案建議直接用 types.Part.from_bytes 走 inline，
    沒有金鑰綁定問題也省一趟上傳。
    """
    cfg = config_ if config_ is not None else kwargs.pop("config", None)
    kwargs.pop("model", None)          # 一律走 MODEL_CHAIN，忽略呼叫端指定
    keys = _keys()
    if not keys:
        raise RuntimeError("未設定任何 GEMINI_API_KEY")
    last_exc: Exception | None = None

    for round_i in range(attempts):
        tried_any = False
        for key_idx in range(len(keys)):
            client = _client(key_idx, timeout_ms)
            body = None                # 每組金鑰只解析（上傳）一次
            for model in MODEL_CHAIN:
                slot = (key_idx, model)
                if slot in _exhausted:
                    continue
                tried_any = True
                try:
                    if body is None:
                        body = contents(client) if callable(contents) else contents
                    resp = client.models.generate_content(
                        model=model, contents=body, config=cfg, **kwargs)
                    _fail_streak.pop(slot, None)
                    return resp
                except errors.APIError as e:
                    last_exc = e
                    tag = f"金鑰#{key_idx + 1} {model}"
                    if e.code == 429 and "PerDay" in str(e):
                        _exhausted.add(slot)
                        log.warning("%s 每日額度耗盡，換下一組", tag)
                    elif e.code == 429:
                        log.warning("%s 限流（RPM），換下一組", tag)
                    elif e.code == 404:
                        # 模型已下架（Google 不預告），當次執行不再試
                        _exhausted.add(slot)
                        log.warning("%s 已不存在（404），換下一組", tag)
                    elif e.code in _RETRYABLE_CODES:
                        n = _fail_streak.get(slot, 0) + 1
                        _fail_streak[slot] = n
                        if n >= _DOWN_LIMIT:
                            _exhausted.add(slot)
                            log.warning("%s 連續 %d 次回 %s，本次執行不再嘗試",
                                        tag, n, e.code)
                        else:
                            log.warning("%s 回 %s，換下一組", tag, e.code)
                    else:
                        raise  # 4xx 邏輯錯誤（如 prompt 格式），換模型也沒用
                except (httpx.HTTPError, ConnectionError) as e:
                    last_exc = e
                    log.warning("金鑰#%d %s 連線層錯誤 %s，換下一組",
                                key_idx + 1, model, type(e).__name__)

        if not tried_any:
            break  # 全部組合都耗盡，再掃也沒用
        if round_i < attempts - 1:
            wait = 30 * (round_i + 1)
            log.warning("所有金鑰×模型本輪皆失敗，%d 秒後重掃", wait)
            time.sleep(wait)

    raise RuntimeError(f"所有 Gemini 金鑰與模型都失敗，最後錯誤：{last_exc}")
