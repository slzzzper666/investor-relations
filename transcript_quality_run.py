"""逐字稿品質回補驅動程式：依序（單一程序，不吃滿電腦）執行
  1. 重聽 data/proofread/retranscribe_ids.txt 列的場次（Whisper 誤判語言、亂翻英文的舊稿），
     按月份分段查 MOPS（一次查太多個月容易逾時）
  2. 其餘舊稿批次文字校對（proofread_backfill.py；Gemini 主力模型額度用完就停）
兩步都冪等，可隨時重跑續做。輸出附加到 data/transcript_quality.log。
"""
import calendar
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IDS = ROOT / "data" / "proofread" / "retranscribe_ids.txt"
LOG = ROOT / "data" / "transcript_quality.log"


def run(args: list[str]) -> None:
    with LOG.open("a", encoding="utf-8") as fh:
        subprocess.run([sys.executable, "-u", *args], cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)


def months_of(ids: list[str]) -> list[tuple[str, str]]:
    ms = sorted({i.split("_")[1][:7] for i in ids})
    out = []
    for m in ms:
        y, mo = map(int, m.split("-"))
        last = calendar.monthrange(y, mo)[1]
        out.append((f"{m}-01", f"{m}-{last:02d}"))
    return out


DONE_IDS = ROOT / "data" / "proofread" / "retranscribed.txt"
TODO_IDS = ROOT / "data" / "proofread" / "retranscribe_todo.txt"


def main() -> None:
    def lines(f: Path) -> list[str]:
        return [l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] if f.exists() else []
    done = set(lines(DONE_IDS))
    ids = [i for i in lines(IDS) if i not in done]          # 已重聽成功的不再重做
    TODO_IDS.write_text("".join(i + "\n" for i in ids), encoding="utf-8")
    for since, until in months_of(ids):
        run(["backfill_transcripts.py", "--since", since, "--until", until, "--ids-file", str(TODO_IDS)])
    run(["proofread_backfill.py"])


if __name__ == "__main__":
    main()
