import json
with open(r"D:\AI\Investor_Relations\data\claude_queue\batch.json", encoding="utf-8") as f:
    data = json.load(f)
for item in data:
    out_path = rf"D:\AI\Investor_Relations\data\claude_queue\b2_{item['id']}.txt"
    with open(out_path, "w", encoding="utf-8") as o:
        o.write(f"=== {item['id']} | {item['company']} | {item['source']} ===\n")
        o.write(item["content"])
    print(f"Written: {item['id']} | {item['company']} | len={len(item['content'])}")
