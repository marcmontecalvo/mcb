import json, base64, zlib

parts = []
with open('results/companion/20261109T025329174604Z/events.jsonl', 'r', encoding='utf-8', errors='replace') as f:
    for idx, line in enumerate(f):
        if 'plan-282' in line and '"kind":"part"' in line:
            ev = json.loads(line)
            parts.append(ev['payload']['data'])

encoded = ''.join(parts)
decompressed = zlib.decompress(base64.b64decode(encoded))
data = json.loads(decompressed)
print("Plan details:")
print("plan_id:", data.get("plan_id"))
print("start:", data.get("start"))
print("goal:", data.get("goal"))
print("interact_reach:", data.get("interact_reach"))
print("mode:", data.get("mode"))
print("radius:", data.get("radius"))
print("policy:", data.get("policy"))
