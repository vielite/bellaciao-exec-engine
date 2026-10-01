#!/usr/bin/env python3
"""Pull final JSON records out of analyzer transcripts into RECORDS.jsonl (dedup by analysisFile)."""
import glob, json, re, sys
TASKS = '/tmp/claude-1000/-home-vielite-hackenProof-sui/29cad29e-d94a-4454-97cb-c42813a2f889/tasks/*.output'
REC = '/home/vielite/hackenProof/sui/audit-context/RECORDS.jsonl'

def texts(path):
    for line in open(path, errors='replace'):
        try: d = json.loads(line)
        except Exception: continue
        m = d.get('message') or {}
        c = m.get('content') if isinstance(m, dict) else None
        if not isinstance(c, list) or d.get('type') != 'assistant': continue
        for b in c:
            if b.get('type') == 'text': yield b['text']
            elif b.get('type') == 'tool_use':
                inp = b.get('input') or {}
                for v in inp.values():
                    if isinstance(v, str) and '"analysisFile"' in v: yield v

def extract(s):
    i = s.find('{')
    while i != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(s[i:])
            if isinstance(obj, dict) and 'analysisFile' in obj: return obj
        except Exception: pass
        i = s.find('{', i + 1)

have = {}
for line in open(REC):
    if line.strip():
        r = json.loads(line); have[r['analysisFile']] = r
new = 0
for p in glob.glob(TASKS):
    rec = None
    for t in texts(p):
        r = extract(t)
        if r: rec = r
    if rec and rec['analysisFile'] not in have:
        m = re.search(r'functions/(\d\d)-', rec['analysisFile']); rec['_task'] = int(m.group(1)) if m else None
        have[rec['analysisFile']] = rec; new += 1
        print('harvested', rec['_task'], rec['function'][:70])
with open(REC, 'w') as f:
    for r in sorted(have.values(), key=lambda r: r.get('_task') or 99): f.write(json.dumps(r) + '\n')
done = sorted(r.get('_task') for r in have.values())
print(f'new={new} total={len(have)} done={done}')
print('missing=', [i for i in range(1, 31) if i not in done])
