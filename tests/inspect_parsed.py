"""Inspect a parsed JSON document — shows the contract shape at a glance.

Usage: python tests/inspect_parsed.py out/chunk_expert_parsed.json
"""

import json
import sys
from pathlib import Path

path = sys.argv[1] if len(sys.argv) > 1 else "out/chunk_expert_parsed.json"
d = json.loads(Path(path).read_text(encoding="utf-8"))

if "documents" in d:
    d = d["documents"][0]

print(f"source_id : {d['source_id']}")
print(f"format    : {d['format_type']}")
print(f"meta      : {json.dumps(d['meta'], ensure_ascii=False)}")
print(f"engine    : {d['stats']['parser_engine_used']}")
c = d["stats"].get("cleaning")
if c:
    print(f"cleaning  : {c['blocks_before']} -> {c['blocks_after']} "
          f"(removed {c['blocks_removed']}, applied: {', '.join(c['cleaners_applied']) or 'none'})")
print(f"sections  : {len(d['sections'])}")

types = {}
for s in d["sections"]:
    types[s["type"]] = types.get(s["type"], 0) + 1
print(f"block types: {json.dumps(types, ensure_ascii=False)}")

print()
print("--- first 8 sections (heading_path = breadcrumb for downstream chunking) ---")
for s in d["sections"][:8]:
    hp = " > ".join(s["heading_path"]) if s["heading_path"] else "(root)"
    loc = json.dumps(s["location"], ensure_ascii=False) if s["location"] else "{}"
    print(f"  [{s['block_id']:3d}] {s['type']:<11} lvl={s['level']} loc={loc}")
    print(f"        path: {hp}")
    print(f"        text: {s['content'][:72]!r}")
