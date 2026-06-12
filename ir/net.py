"""網路層：自動偵測是否需要 Proxy（家裡直連 / 公司 proxy.capital.com.tw:8080）。"""
import os

import requests

from ir.logger import get_logger

log = get_logger("ir.net")

_PROBE_URL = "https://www.google.com/generate_204"
_session: requests.Session | None = None


def _direct_ok(timeout: int = 5) -> bool:
    try:
        requests.get(_PROBE_URL, timeout=timeout, proxies={"http": None, "https": None})
        return True
    except requests.RequestException:
        return False


def get_session() -> requests.Session:
    """回傳已設定好 Proxy 的 Session（整個程式共用，只偵測一次）。"""
    global _session
    if _session is not None:
        return _session

    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.0.0 Safari/537.36"),
    })

    if _direct_ok():
        log.info("網路偵測：直連可用，不使用 Proxy")
    else:
        proxy = os.getenv("FALLBACK_PROXY", "http://proxy.capital.com.tw:8080")
        s.proxies = {"http": proxy, "https": proxy}
        # 讓 yt-dlp、SDK 等其他套件也吃到 Proxy
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
        log.info("網路偵測：直連失敗，改用 Proxy %s", proxy)

    _session = s
    return s
