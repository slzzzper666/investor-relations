import json, sys
data = open('D:/AI/Investor_Relations/data/claude_queue/results.json', encoding='utf-8').read()
arr = json.loads(data)
print(f'Valid JSON, {len(arr)} entries')
for i, x in enumerate(arr):
    print(f'  {i+1}. {x["id"]}')
