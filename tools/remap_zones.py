"""Moves desk zones between cameras after channel numbers were corrected.
Usage: python tools/remap_zones.py data/zones.json new_id=old_id [new_id=old_id ...]"""
import json
import shutil
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    print("no zones.json yet - nothing to move")
    sys.exit(0)
shutil.copy(path, path.with_suffix(".json.bak"))
old = json.loads(path.read_text(encoding="utf-8"))
moves = dict(a.split("=", 1) for a in sys.argv[2:])
new = {k: v for k, v in old.items() if k not in moves}
for new_id, old_id in moves.items():
    if old_id in old:
        new[new_id] = old[old_id]
        print(f"  desks drawn under '{old_id}' -> '{new_id}' ({len(old[old_id].get('desks', []))} desks)")
path.write_text(json.dumps(new, indent=2, ensure_ascii=False), encoding="utf-8")
