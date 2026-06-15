import json
with open(r"D:\AI\Investor_Relations\data\claude_queue\results.json", encoding="utf-8") as f:
    data = json.load(f)
print(f"Valid JSON: {len(data)} records")
for x in data:
    print(x["id"])
