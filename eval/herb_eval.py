"""Score graph queries against HERB's own ground truth for the "person" questions whose
answer is a pure graph pattern. Exact set match on employee ids; ties count as a set.

    python -m eval.herb_eval data-raw/herb
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from lore.db import graph

ROLES = {"Marketing Research Analysts": "Marketing Research Analyst", "Product Managers": "Product Manager",
         "Technical Architects": "Technical Architect", "Engineering Leads": "Engineering Lead",
         "QA specialists": "QA Specialist"}

# template regex -> (name, cypher). $p = product, $role = role.
TEMPLATES = [
    (re.compile(r"Find employee IDs of (.+?) who worked on the previous release of (\w+)\?"), "previous-release authors by role", """
        MATCH (newer:Document)-[:SUPERSEDES]->(d:Document)-[:DOCUMENTS]->(:Product {name: $p})
        MATCH (d)-[:AUTHORED_BY]->(a:Person {role: $role})
        RETURN collect(DISTINCT a.id) AS ids"""),
    (re.compile(r"Find employee IDs of engineers who are assigned to the highest number of active bugs in (\w+)\."), "most active bugs assigned", """
        MATCH (:Product {name: $p})<-[:PART_OF]-(:Component)<-[:IN_COMPONENT]-(i:Issue)-[asg:ASSIGNED_TO]->(a:Person)
        WHERE i.status <> 'resolved' AND asg.until IS NULL
        WITH a, count(DISTINCT i) AS n WITH collect({id: a.id, n: n}) AS rows, max(n) AS top
        RETURN [r IN rows WHERE r.n = top | r.id] AS ids"""),
    (re.compile(r"Find the employee IDs of engineers who authored maximum number of PRs in (\w+) for bug fixes that are now reopened by customers\."), "most fixes later reopened", """
        MATCH (:Product {name: $p})<-[:PART_OF]-(:Component)<-[:IN_COMPONENT]-(i:Issue {status: 'reopened'})<-[:RESOLVED]-(a:Person)
        WITH a, count(DISTINCT i) AS n WITH collect({id: a.id, n: n}) AS rows, max(n) AS top
        RETURN [r IN rows WHERE r.n = top | r.id] AS ids"""),
]


def main(root: Path):
    g = graph()
    score = defaultdict(lambda: [0, 0])
    misses = []
    for f in sorted((root / "products").glob("*.json")):
        for q in json.loads(f.read_text(encoding="utf-8"))["answerable_questions"]:
            if q["type"] != "person":
                continue
            for rx, name, cypher in TEMPLATES:
                m = rx.match(q["question"])
                if not m:
                    continue
                if name.startswith("previous"):
                    role = ROLES.get(m.group(1))
                    if not role:
                        break
                    params = {"p": m.group(2), "role": role}
                else:
                    params = {"p": m.group(1)}
                got = set(g.ro_query(cypher, params).result_set[0][0])
                ok = got == set(q["ground_truth"])
                score[name][0] += ok
                score[name][1] += 1
                if not ok:
                    misses.append((name, q["question"], sorted(got), sorted(q["ground_truth"])))
                break
    total = [sum(v[0] for v in score.values()), sum(v[1] for v in score.values())]
    for name, (ok, n) in score.items():
        print(f"{name:34s} {ok:3d}/{n:<3d}")
    print(f"{'total':34s} {total[0]:3d}/{total[1]:<3d}")
    for m in misses[:12]:
        print("MISS", m)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "data-raw/herb"))
