FROM python:3.12-slim

# stdout 即時輸出（Railway log 依賴 stdout），不產生 .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Taipei

WORKDIR /app

# 先裝依賴以利用 Docker layer cache
# 注意：imageio-ffmpeg 的 wheel 自帶 Linux ffmpeg 執行檔，毋須 apt 安裝 ffmpeg；
#       lxml 等套件在 3.12-slim 上有官方 manylinux wheel，毋須編譯工具。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
