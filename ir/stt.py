"""階段三：語音轉文字。

優先 Groq Whisper（快、額度大）；大檔自動切段再逐段轉錄。
沒有 GROQ_API_KEY 才用 Gemini 整段轉錄（lite 模型對長音檔會迴圈，盡量避免）。
"""
import re
import subprocess
import tempfile
import time
from pathlib import Path

from google.genai import types

import config
from ir.gemini_util import generate_with_retry
from ir.logger import get_logger
from ir.stt_hints import SttHints

log = get_logger("ir.stt")

_PROMPT = (
    "這是一場台灣上市櫃公司法人說明會的錄音。"
    "請將內容完整轉成繁體中文逐字稿，要求：\n"
    "1. 全程使用繁體中文（台灣用語），專有名詞、英文縮寫保留原文\n"
    "2. 忽略開場等待音樂與雜音\n"
    "3. 不同發言人換行，可標示「主持人：」「公司代表：」「提問：」等角色\n"
    "4. 只輸出逐字稿本身，不要加任何前言或說明"
)


def _upload_and_wait(client, path: Path):
    f = client.files.upload(file=str(path))
    while f.state and f.state.name == "PROCESSING":
        time.sleep(5)
        f = client.files.get(name=f.name)
    if f.state and f.state.name == "FAILED":
        raise RuntimeError(f"Gemini 檔案處理失敗：{path.name}")
    return f


_local_model = None


def _add_cuda_dll_dirs():
    """把 pip 安裝的 CUDA 12 runtime/cuBLAS/cuDNN DLL 目錄加入搜尋路徑（GPU 必要）。

    ctranslate2 載 DLL 同時依賴 PATH 與 add_dll_directory，兩者都加。
    """
    import os
    import sysconfig
    site = Path(sysconfig.get_paths()["purelib"])
    dirs = []
    for sub in ("cuda_runtime", "cublas", "cudnn"):
        d = site / "nvidia" / sub / "bin"
        if d.is_dir():
            dirs.append(str(d))
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")


def _get_local_model():
    """載入本地 Whisper large-v3（單例，整個程序共用）。GPU 失敗退 CPU。"""
    global _local_model
    if _local_model is not None:
        return _local_model
    _add_cuda_dll_dirs()
    from faster_whisper import WhisperModel
    for device, compute in (("cuda", "float16"), ("cpu", "int8")):
        try:
            _local_model = WhisperModel(
                "large-v3", device=device, compute_type=compute, cpu_threads=8)
            log.info("本地 Whisper large-v3 已載入（device=%s）", device)
            return _local_model
        except Exception as e:
            log.warning("本地 Whisper device=%s 載入失敗：%s", device, str(e)[:150])
    raise RuntimeError("本地 Whisper 無法載入")


def _transcribe_local(audio_path: Path, hints: SttHints | None = None) -> str:
    model = _get_local_model()
    # 語言由 hints 指定（依錄影檔名 _ch／_en，見 ir/stt_hints）：自動偵測會把中英夾雜的
    # 中文場判成英文並自行翻譯。initial_prompt 帶公司名與術語，降低同音錯字。
    h = hints or SttHints()
    segments, info = model.transcribe(
        str(audio_path), beam_size=1, language=h.language,
        initial_prompt=h.prompt or None,
        vad_filter=True, vad_parameters=dict(min_silence_duration_ms=500))
    text = "".join(seg.text for seg in segments).strip()
    if len(text) < 100:
        raise RuntimeError(f"逐字稿過短（{len(text)} 字），疑似轉錄失敗")
    log.info("本地逐字稿完成：%s（音檔 %.0fs，%d 字）",
             audio_path.name, info.duration, len(text))
    return _collapse_loops(text)


def transcribe(audio_path: Path, hints: SttHints | None = None) -> str:
    """音檔 → 逐字稿。hints：語言與提示詞（見 ir/stt_hints）。失敗丟例外。"""
    if getattr(config, "USE_LOCAL_WHISPER", False):
        try:
            return _transcribe_local(audio_path, hints)
        except Exception as e:
            log.warning("本地 Whisper 失敗，改用雲端 STT：%s", e)

    if config.GROQ_API_KEY:
        try:
            size_mb = audio_path.stat().st_size / 1e6
            if size_mb < 24:  # Groq 免費版單檔上限 25MB
                return _collapse_loops(_transcribe_groq(audio_path, hints))
            return _collapse_loops(_transcribe_groq_chunked(audio_path, hints=hints))
        except Exception as e:
            log.warning("Groq 轉錄失敗，改用 Gemini：%s", e)

    log.info("上傳音檔到 Gemini：%s（%.1f MB）", audio_path.name,
             audio_path.stat().st_size / 1e6)

    # 音檔動輒數十 MB，超過單次請求上限、只能走 Files API，而上傳的檔案綁定
    # 該金鑰。所以這裡把「上傳」包成 callable：輪替到哪組金鑰就用哪組上傳，
    # gemini_util 每組金鑰只會呼叫一次。
    uploaded: list[tuple] = []

    prompt = _PROMPT + (f"\n5. 專有名詞參考：{hints.prompt}" if hints and hints.prompt else "")

    def _build(client):
        f = _upload_and_wait(client, audio_path)
        uploaded.append((client, f))
        return [f, prompt]

    try:
        resp = generate_with_retry(
            _build,
            config_=types.GenerateContentConfig(temperature=0.1),
            timeout_ms=900_000,      # 長音檔轉錄很慢，拉長 HTTP 逾時
        )
        text = (resp.text or "").strip()
    finally:
        for client, f in uploaded:
            try:
                client.files.delete(name=f.name)
            except Exception:
                pass

    if len(text) < 100:
        raise RuntimeError(f"逐字稿過短（{len(text)} 字），疑似轉錄失敗")
    log.info("逐字稿完成：%s（%d 字）", audio_path.name, len(text))
    return _collapse_loops(text)


# Whisper 在靜音／音樂段會吐出訓練資料裡的影片片尾字樣（2026-10 查到 169 篇有）。
# 只列「法說會不可能出現」的固定字串，逐字比對刪除。
HALLUCINATIONS = re.compile(
    r"請不吝點[贊讚]\s*訂閱\s*轉發\s*打賞支持明鏡與點點欄目"
    r"|请不吝点[赞讚]\s*订阅\s*转发\s*打赏支持明镜与点点栏目"
    r"|字幕由\s*Amara\.org\s*社[群区區]提供|(?:中文)?字幕提供")


def strip_hallucinations(text: str) -> str:
    return HALLUCINATIONS.sub("", text or "")


def _collapse_loops(text: str) -> str:
    """模型轉錄長音檔偶爾會陷入重複迴圈，把連續重複的行壓成一行；順便刪掉幻聽字樣。"""
    text = strip_hallucinations(text)
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        if out and line.strip() and line.strip() == out[-1].strip():
            continue
        out.append(line)
    collapsed = "\n".join(out)
    # 多行區塊整段重複（A B C A B C…）：偵測到大量重複時砍到第一次出現處
    for m in re.finditer(r"(.{80,400}?)\1{3,}", collapsed, flags=re.DOTALL):
        collapsed = collapsed[:m.start() + len(m.group(1))] + collapsed[m.end():]
        log.warning("逐字稿偵測到重複迴圈，已截斷 %d 字", m.end() - m.start() - len(m.group(1)))
        break
    return collapsed


def _transcribe_groq_chunked(audio_path: Path, segment_sec: int = 1200,
                             hints: SttHints | None = None) -> str:
    """大檔切成 20 分鐘段落逐段轉錄（64kbps 下每段約 9.6MB）。"""
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory() as td:
        pattern = str(Path(td) / "chunk_%03d.mp3")
        r = subprocess.run(
            [ffmpeg, "-y", "-i", str(audio_path), "-c", "copy",
             "-f", "segment", "-segment_time", str(segment_sec), pattern],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            raise RuntimeError(f"音檔切段失敗：{r.stderr[-200:]}")
        chunks = sorted(Path(td).glob("chunk_*.mp3"))
        log.info("%s 切成 %d 段逐段轉錄", audio_path.name, len(chunks))
        parts = [_transcribe_groq(c, hints) for c in chunks]
    return "\n".join(parts)


def _transcribe_groq(audio_path: Path, hints: SttHints | None = None) -> str:
    """Groq Whisper（whisper-large-v3-turbo）。檔案上限 25MB。"""
    import requests

    data = {"model": "whisper-large-v3-turbo", "response_format": "text"}
    if hints and hints.language:
        data["language"] = hints.language     # 不給就自動偵測，中英夾雜時會誤判成英文並翻譯
    if hints and hints.prompt:
        data["prompt"] = hints.prompt
    with open(audio_path, "rb") as fh:
        r = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            files={"file": (audio_path.name, fh, "audio/mpeg")},
            data=data,
            timeout=600,
        )
    r.raise_for_status()
    text = r.text.strip()
    log.info("Groq 逐字稿完成：%s（%d 字）", audio_path.name, len(text))
    return text
