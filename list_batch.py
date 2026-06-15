import json
with open(r"D:\AI\Investor_Relations\data\claude_queue\batch.json", encoding="utf-8") as f:
    data = json.load(f)
for item in data:
    print(item["id"], "|", item["company"], "|", item["date"], "|", item["source"], "|", len(item["content"]))
