import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
with open(r"D:\AI\Investor_Relations\data\claude_queue\batch.json", encoding="utf-8") as f:
    data = json.load(f)
for item in data:
    if item["id"] in ["6510_2026-05-28", "6510_2026-05-29", "3491_2026-05-27", "2812_2026-05-18", "2610_2026-05-28"]:
        print(f"\n=== {item['id']} | {item['company']} | {item['source']} ===")
        print(item["content"][:8000])
