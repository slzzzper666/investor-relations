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

import imageio_ffmpeg

from ir.logger import get_logger
from ir.mops import Conference

log = get_logger("ir.media")

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

_YT_RE = re.compile(r"(youtube\.com/watch|youtu\.be/|youtube\.com/live)")
_MP4_RE = re.compile(r"\.mp4$", re.IGNORECASE)


def _pick_source(conf: Conference) -> tuple[str, str] | None:
    """回傳 (kind, url)，kind ∈ {direct, youtube}；找不到回傳 None。"""
    mp4s = [u for u in conf.video_urls if _MP4_RE.search(u)]
    if mp4s:
        ch = [u for u in mp4s if "_ch" in u.lower()]
        return ("direct", (ch or mp4s)[0])
    yts = [u for u in conf.video_urls if _YT_RE.search(u)]
    if yts:
        return ("youtube", yts[0])
    return None


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
        log.warning("ffmpeg 抽取失敗 %s：%s", url, r.stderr[-300:] if r.stderr else "")
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
