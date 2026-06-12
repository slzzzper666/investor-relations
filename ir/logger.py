"""統一 logging：同時輸出到 CMD 與 investor_relations.log，時間用 Asia/Taipei。"""
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")

LOG_FILE = "investor_relations.log"


class TaipeiFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=TAIPEI)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def get_logger(name: str = "ir") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = TaipeiFormatter("[%(asctime)s] %(levelname)s %(name)s - %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger
