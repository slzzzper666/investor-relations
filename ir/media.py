"""階段二：影音來源解析與音檔萃取。

來源優先序：
  1. MOPS 登載的直接影音檔（irconference.twse.com.tw 的 mp4，優先中文版 _ch）
  2. MOPS 登載的 YouTube 連結
  3. yt-dlp ytsearch 搜尋「公司名 法說會」（不需 YouTube API key）
其餘公司自家網頁、webex 等來源無法通用化處理，視為找不到影片。

輸出一律轉成 16kHz 單聲道 mp3（檔案小、Gemini/Whisper 都支援）。
"""
import re
import subprocess
from pathlib import Path

import shutil

import imageio_ffmpeg

from ir.logger import get_logger
from ir.mops import Conference

log = get_logger("ir.media")

def _find_ffmpeg() -> str:
    """優先用系統 ffmpeg，找不到才退回 imageio-ffmpeg 附帶的執行檔。

    2026-09 下旬起 irconference 的 HTTPS 連線會讓 imageio-ffmpeg 的 Linux 靜態版
    （johnvansickle 7.0.2）直接 Segmentation fault——連以前成功過的網址也一樣，
    stderr 只剩版本資訊、沒有錯誤訊息。Debian 套件版 ffmpeg 正常。
    Railway 的 Dockerfile 會 apt 安裝 ffmpeg；本機 Windows 沒有就用 imageio 版（Windows 版無此問題）。
    """
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


FFMPEG = _find_ffmpeg()

_YT_RE = re.compile(r"(youtube\.com/watch|youtu\.be/|youtube\.com/live)")
# 直接可下載的音/視訊檔（irconference 有 .mp4 也有 .mp3）
_MEDIA_RE = re.compile(r"\.(mp4|mp3|m4a|wav|mov|avi|wmv)$", re.IGNORECASE)


def _force_https(url: str) -> str:
    """irconference 自 2026 年中起把 http 強制 301 轉 https，但 MOPS 登載的仍是
    http 連結。ffmpeg 讀到這個轉址會卡住直到逾時（實測 http 卡死 60 秒以上、
    https 19 秒完成），所以交給 ffmpeg 前一律改成 https。"""
    if url.lower().startswith("http://irconference.twse.com.tw"):
        return "https://" + url[len("http://"):]
    return url


def _pick_source(conf: Conference) -> tuple[str, str] | None:
    """回傳 (kind, url)，kind ∈ {direct, youtube}；找不到回傳 None。"""
    media = [u for u in conf.video_urls if _MEDIA_RE.search(u)]
    if media:
        ch = [u for u in media if "_ch" in u.lower()]
        return ("direct", _force_https((ch or media)[0]))
    yts = [u for u in conf.video_urls if _YT_RE.search(u)]
    if yts:
        return ("youtube", yts[0])
    return None


_OFFICIAL_CHANNELS = ("證券交易所", "櫃買", "TWSE", "TPEx", "證券", "Securities", "IR",
                      "新聞", "NEWS", "News", "電視")


def official_channel(channel: str, company: str) -> bool:
    """公司／交易所／券商／電視新聞頻道才收。公司頻道常是英文名（ICP DAS、AAEON、Swancor），
    所以「完全沒有中文字」的頻道也收；評論頻道（價值股雷達、恥股夯妮、艾伯納的投資筆記…）都是中文名。"""
    short = re.sub(r"[*＊]|-KY$", "", company)
    return (short in channel or any(k in channel for k in _OFFICIAL_CHANNELS)
            or not re.search(r"[一-鿿]", channel))


def _yt_search_pick(conf: Conference) -> str | None:
    """ytsearch 搜 5 筆，挑標題含公司名且像法說會的影片，回傳影片 URL。"""
    import yt_dlp

    query = f"ytsearch5:{conf.company_name} 法說會 {conf.date.year}"
    opts = {"quiet": True, "noprogress": True, "extract_flat": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
    except Exception as e:
        log.warning("YouTube 搜尋失敗 %s：%s", query, e)
        return None

    # 完整字樣才算數：「法說」兩字會誤抓第三方評論片（如「從法說崩跌到…」）
    keywords = ("法說會", "法人說明會", "業績發表", "investor conference")
    for entry in (info or {}).get("entries") or []:
        title = entry.get("title", "")
        duration = entry.get("duration") or 0
        if conf.company_name not in title:
            continue
        if not any(k in title.lower() for k in keywords):
            continue
        if duration and duration < 600:  # 短於 10 分鐘的多半是新聞剪輯
            continue
        # 上傳頻道必須是公司本身或交易所／券商：2026-10 查到「價值股雷達」等評論頻道
        # 標題寫「穩懋 法說會」，被當成法說會錄影轉成逐字稿（穩懋 5 場全是評論片）
        channel = (entry.get("channel") or entry.get("uploader") or "")
        if not official_channel(channel, conf.company_name):
            log.info("%s %s：略過非官方頻道「%s」的影片「%s」", conf.stock_code,
                     conf.company_name, channel, title)
            continue
        url = entry.get("url") or entry.get("webpage_url")
        if not url or not _upload_date_ok(url, conf):
            continue
        log.info("%s %s：YouTube 搜尋命中「%s」", conf.stock_code,
                 conf.company_name, title)
        return url
    return None


def _upload_date_ok(url: str, conf: Conference, window_days: int = 45) -> bool:
    """確認影片上傳日在法說會日期 ±window_days 內，避免抓到舊場次或評論片。"""
    import yt_dlp
    from datetime import date, timedelta

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "noprogress": True}) as ydl:
            meta = ydl.extract_info(url, download=False, process=False)
        raw = (meta or {}).get("upload_date")  # YYYYMMDD
        if not raw:
            return True  # 拿不到日期就放行，交給片長與標題濾網
        uploaded = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        ok = abs((uploaded - conf.date).days) <= window_days
        if not ok:
            log.info("%s %s：影片上傳日 %s 與法說會 %s 差距過大，捨棄",
                     conf.stock_code, conf.company_name, uploaded, conf.date)
        return ok
    except Exception:
        return True


def _ytdlp_download_audio(url: str, out_base: Path) -> Path | None:
    """用 yt-dlp 下載並抽出 mp3 音軌。"""
    import yt_dlp

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_base) + ".%(ext)s",
        "ffmpeg_location": FFMPEG,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "64",
        }],
        "postprocessor_args": ["-ac", "1", "-ar", "16000"],
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        if info and "entries" in info:  # ytsearch 結果
            entries = info["entries"]
            if not entries:
                return None
            info = entries[0]
        mp3 = out_base.with_suffix(".mp3")
        if mp3.exists():
            title = (info or {}).get("title", "")
            log.info("yt-dlp 音檔完成：%s（%s）", mp3.name, title)
            return mp3
    except Exception as e:
        log.warning("yt-dlp 下載失敗 %s：%s", url, e)
    return None


def _direct_download_audio(url: str, out_base: Path) -> Path | None:
    """ffmpeg 直接從遠端 mp4 抽音軌（不必先載完整部影片）。"""
    mp3 = out_base.with_suffix(".mp3")
    tmp = out_base.with_suffix(".tmp.mp3")  # 中斷不留下不完整的 .mp3
    cmd = [FFMPEG, "-y", "-i", url, "-vn", "-ac", "1", "-ar", "16000",
           "-b:a", "64k", "-f", "mp3", str(tmp)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 10_000:
            tmp.replace(mp3)
            log.info("直接抽取音檔完成：%s（%.1f MB）", mp3.name,
                     mp3.stat().st_size / 1e6)
            return mp3
        # 帶上結束碼：負值＝被訊號終止（-11 是崩潰），stderr 這時往往只有版本資訊
        log.warning("ffmpeg 抽取失敗（rc=%s）%s：%s", r.returncode, url,
                    r.stderr[-300:] if r.stderr else "")
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg 抽取逾時：%s", url)
    return None


def get_audio(conf: Conference, dest_dir: Path) -> tuple[Path | None, str]:
    """取得法說會音檔。回傳 (mp3路徑或None, 影片來源URL或'')。"""
    out_base = dest_dir / f"{conf.stock_code}_{conf.date.isoformat()}"
    mp3 = out_base.with_suffix(".mp3")

    src = _pick_source(conf)
    if src:
        kind, url = src
        if mp3.exists():
            return mp3, url
        log.info("%s %s：使用 MOPS 登載來源（%s）%s",
                 conf.stock_code, conf.company_name, kind, url)
        path = (_direct_download_audio(url, out_base) if kind == "direct"
                else _ytdlp_download_audio(url, out_base))
        if path:
            return path, url
        # MOPS 給的連結失效就退回 YouTube 搜尋

    yt_url = _yt_search_pick(conf)
    if yt_url:
        if mp3.exists():
            return mp3, yt_url
        path = _ytdlp_download_audio(yt_url, out_base)
        if path:
            return path, yt_url
    log.info("%s %s：找不到可用影音，僅以 PDF 處理", conf.stock_code, conf.company_name)
    return None, ""
