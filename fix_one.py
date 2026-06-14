"""一次性重跑單一場法說會（修復用）。
用法：python fix_one.py <股票代號> <YYYY-MM-DD>
"""
import sys
from datetime import date

from ir.mops import get_conferences
from main import process_one

code, d = sys.argv[1], date.fromisoformat(sys.argv[2])
conf = next(c for c in get_conferences(d) if c.stock_code == code)
print(f"重跑 {conf.stock_code} {conf.company_name}：PDF={conf.pdf_filename or '無'}，"
      f"影音={conf.video_urls}")
process_one(conf, push=False)
print("完成")
