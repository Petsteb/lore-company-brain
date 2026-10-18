"""Load LLM-extracted decisions/commitments with provenance checks.

Extraction was done offline by Claude over meeting transcripts (see
data-derived/decisions/). At load time every item must quote its transcript
verbatim or it is rejected, and speaker/owner names are resolved to employee
ids only among that meeting's participants. If a name is shared by several
participants, all of them are linked and the edge is marked ambiguous.
"""
from __future__ import annotations

import json
from pathlib import Path

from .db import graph


def load_decisions(raw_root: Path, derived_root: Path) -> dict:
    g = graph()
    employees = json.loads((raw_root / "metadata/employee.json").read_text(encoding="utf-8"))
    rows, people_edges, revises, rejected = [], [], [], []
    for f in sorted(derived_root.glob("*.json")):
        product = f.stem
        src = json.loads((raw_root / f"products/{product}.json").read_text(encoding="utf-8"))
        meetings = {m["id"]: m for m in src["meeting_transcripts"]}
        for d in json.loads(f.read_text(encoding="utf-8")):
            m = meetings.get(d["source_id"])
            if not m or d["quote"] not in m["transcript"]:
                rejected.append(d["id"])
                continue
            rows.append({"id": d["id"], "kind": d["kind"], "summary": d["summary"], "quote": d["quote"],
                         "topic": d.get("topic") or "", "at": d["at"], "meeting": d["source_id"]})
            participants = set(m.get("participants", []))
            for rel, name in (("SPOKEN_BY", d.get("speaker")), ("OWNED_BY", d.get("owner"))):
                if not name:
                    continue
                matches = [e for e in participants if employees.get(e, {}).get("name", "").lower() == name.strip().lower()]
                for e in matches:
                    people_edges.append({"d": d["id"], "p": e, "rel": rel, "ambiguous": len(matches) > 1})
            if d.get("revises"):
                revises.append({"new": d["id"], "old": d["revises"]})
    g.query("MATCH (d:Decision) DETACH DELETE d")
    g.query("""UNWIND $rows AS r MATCH (m:Meeting {id: r.meeting})
               CREATE (d:Decision {id: r.id, kind: r.kind, summary: r.summary, quote: r.quote, topic: r.topic, at: r.at})
               CREATE (d)-[:DECIDED_IN]->(m)""", {"rows": rows})
    for rel in ("SPOKEN_BY", "OWNED_BY"):
        g.query(f"""UNWIND $rows AS r MATCH (d:Decision {{id: r.d}}), (p:Person {{id: r.p}})
                    CREATE (d)-[:{rel} {{ambiguous: r.ambiguous}}]->(p)""",
                {"rows": [e for e in people_edges if e["rel"] == rel]})
    g.query("""UNWIND $rows AS r MATCH (n:Decision {id: r.new}), (o:Decision {id: r.old})
               CREATE (n)-[:REVISES]->(o)""", {"rows": revises})
    return {"decisions": len(rows), "people_links": len(people_edges),
            "ambiguous_links": sum(e["ambiguous"] for e in people_edges), "revises": len(revises), "rejected": rejected}
