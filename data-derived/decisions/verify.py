"""Check every extracted item: quote must be a verbatim substring of its source transcript."""
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[2]
prod = sys.argv[1]
src = json.loads((root / f"data-raw/herb/products/{prod}.json").read_text(encoding="utf-8"))
tx = {m["id"]: m["transcript"] for m in src["meeting_transcripts"]}
items = json.loads((Path(__file__).parent / f"{prod}.json").read_text(encoding="utf-8"))
bad = [i["id"] for i in items if i["source_id"] not in tx or i["quote"] not in tx[i["source_id"]]]
ids = {i["id"] for i in items}
dangling = [i["id"] for i in items if i.get("revises") and i["revises"] not in ids]
print(f"{len(items)} items, {len(bad)} bad quotes {bad[:10]}, {len(dangling)} dangling revises {dangling[:5]}")
