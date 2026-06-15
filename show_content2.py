import json
with open(r"D:\AI\Investor_Relations\data\claude_queue\batch.json", encoding="utf-8") as f:
    data = json.load(f)
targets = ["6510_2026-05-28", "6510_2026-05-29", "3491_2026-05-27", "2812_2026-05-18", "2610_2026-05-28"]
for item in data:
    if item["id"] in targets:
        out_path = rf"D:\AI\Investor_Relations\data\claude_queue\content_{item['id']}.txt"
        with open(out_path, "w", encoding="utf-8") as o:
            o.write(f"=== {item['id']} | {item['company']} | {item['source']} ===\n")
            o.write(item["content"])
        print(f"Written: {out_path}")
