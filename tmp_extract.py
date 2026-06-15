import json, sys, os
sys.stdout.reconfigure(encoding='utf-8')
data = json.loads(open('D:/AI/Investor_Relations/data/claude_queue/batch.json', encoding='utf-8').read())
out_dir = 'D:/AI/Investor_Relations/data/claude_queue/tmp_contents'
os.makedirs(out_dir, exist_ok=True)
for item in data:
    fname = os.path.join(out_dir, f"{item['id']}.txt")
    with open(fname, 'w', encoding='utf-8') as f:
        f.write(f"id: {item['id']}\n")
        f.write(f"company: {item['company']}\n")
        f.write(f"mcap: {item['mcap']}\n")
        f.write(f"source: {item['source']}\n")
        f.write(f"date: {item['date']}\n")
        f.write("---CONTENT---\n")
        f.write(item['content'])
    print(f"Written {fname}")