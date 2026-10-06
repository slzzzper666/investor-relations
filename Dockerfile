FROM python:3.12-slim

# stdout 即時輸出（Railway log 依賴 stdout），不產生 .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Taipei

WORKDIR /app

# 系統 ffmpeg：imageio-ffmpeg 附帶的 Linux 靜態版自 2026-09 下旬起連 irconference 會崩潰
# （Segmentation fault），Debian 套件版正常。ir/media.py 會優先用系統 ffmpeg。
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# 先裝依賴以利用 Docker layer cache
# lxml 等套件在 3.12-slim 上有官方 manylinux wheel，毋須編譯工具。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
