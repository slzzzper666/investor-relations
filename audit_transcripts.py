"""逐字稿品質體檢：揪出重複迴圈、過短、亂碼的逐字稿（免費、不花 token）。"""
import re
from pathlib import Path

import config

CJK = re.compile(r"[一-鿿]")


def audit(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    n = len(lines)
    uniq = len(set(lines))
    uniq_ratio = uniq / n if n else 1.0
    # 最大重複行次數
    from collections import Counter
    max_rep = max(Counter(lines).values()) if lines else 0
    cjk = len(CJK.findall(text))
    cjk_ratio = cjk / len(text) if text else 0
    flags = []
    if len(text) < 300:
        flags.append("過短")
    if uniq_ratio < 0.6 and n > 20:
        flags.append(f"重複率高(uniq={uniq_ratio:.0%})")
    if max_rep > 8:
        flags.append(f"單行重複x{max_rep}")
    if cjk_ratio < 0.25 and len(text) > 300:
        flags.append(f"中文比例低({cjk_ratio:.0%})")
    return {"file": path.name, "chars": len(text), "flags": flags}


def main() -> None:
    files = sorted(config.TRANSCRIPT_DIR.glob("*.txt"))
    print(f"共 {len(files)} 份逐字稿\n")
    bad = []
    for f in files:
        r = audit(f)
        if r["flags"]:
            bad.append(r)
            print(f"⚠ {r['file']}（{r['chars']:,}字）：{'、'.join(r['flags'])}")
    print(f"\n可疑 {len(bad)} 份 / 共 {len(files)} 份")


if __name__ == "__main__":
    main()
