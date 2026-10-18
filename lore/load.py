"""Write parsed HERB rows into FalkorDB.

Every write is an UNWIND over a batch of rows with parameters; nothing is
string-concatenated into Cypher. Run:  python -m lore.load data-raw/herb
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from .db import graph
from .decisions import load_decisions
from .herb import parse

INDEXES = [
    "CREATE INDEX FOR (n:Person) ON (n.id)",
    "CREATE INDEX FOR (n:Person) ON (n.name)",
    "CREATE INDEX FOR (n:Customer) ON (n.id)",
    "CREATE INDEX FOR (n:Product) ON (n.name)",
    "CREATE INDEX FOR (n:Channel) ON (n.id)",
    "CREATE INDEX FOR (n:Message) ON (n.id)",
    "CREATE INDEX FOR (n:Message) ON (n.ts)",
    "CREATE INDEX FOR (n:Document) ON (n.id)",
    "CREATE INDEX FOR (n:Meeting) ON (n.id)",
    "CREATE INDEX FOR (n:PullRequest) ON (n.id)",
    "CREATE INDEX FOR (n:Issue) ON (n.id)",
    "CREATE INDEX FOR (n:Component) ON (n.id)",
    "CREATE INDEX FOR (n:Decision) ON (n.id)",
    "CREATE INDEX FOR (n:Finding) ON (n.ts)",
    # full-text indexes the resolve/search tools use
    "CALL db.idx.fulltext.createNodeIndex('Person', 'name')",
    "CALL db.idx.fulltext.createNodeIndex('Issue', 'title', 'description')",
    "CALL db.idx.fulltext.createNodeIndex('Component', 'name')",
    "CALL db.idx.fulltext.createNodeIndex('Customer', 'name', 'company')",
    "CALL db.idx.fulltext.createNodeIndex('Decision', 'summary', 'quote', 'topic')",
]

# (bucket, cypher). Order matters: nodes before the edges that MATCH them.
WRITES = [
    ("person", "UNWIND $rows AS r CREATE (:Person {id: r.id, name: r.name, role: r.role, location: r.location, org: r.org})"),
    ("customer", "UNWIND $rows AS r CREATE (:Customer {id: r.id, name: r.name, role: r.role, company: r.company})"),
    ("product", "UNWIND $rows AS r CREATE (:Product {name: r.name})"),
    ("reports_to", "UNWIND $rows AS r MATCH (a:Person {id: r.from}), (b:Person {id: r.to}) CREATE (a)-[:REPORTS_TO]->(b)"),
    ("member_of", "UNWIND $rows AS r MATCH (a:Person {id: r.person}), (p:Product {name: r.product}) CREATE (a)-[:MEMBER_OF]->(p)"),
    ("has_customer", "UNWIND $rows AS r MATCH (p:Product {name: r.product}), (c:Customer {id: r.customer}) CREATE (p)-[:HAS_CUSTOMER]->(c)"),
    ("document", """UNWIND $rows AS r MATCH (p:Product {name: r.product})
        CREATE (d:Document {id: r.id, type: r.type, at: r.at, ts: r.ts, link: r.link, content: r.content})-[:DOCUMENTS]->(p)
        WITH d, r MATCH (a:Person {id: r.author}) CREATE (d)-[:AUTHORED_BY]->(a)"""),
    ("supersedes", "UNWIND $rows AS r MATCH (n:Document {id: r.new}), (o:Document {id: r.old}) CREATE (n)-[:SUPERSEDES]->(o)"),
    ("meeting", """UNWIND $rows AS r MATCH (p:Product {name: r.product})
        CREATE (:Meeting {id: r.id, type: r.type, at: r.at, ts: r.ts, transcript: r.transcript})-[:FOR_PRODUCT]->(p)"""),
    ("attended", "UNWIND $rows AS r MATCH (a:Person {id: r.person}), (m:Meeting {id: r.meeting}) CREATE (a)-[:ATTENDED]->(m)"),
    ("pr", """UNWIND $rows AS r MERGE (x:PullRequest {id: r.id})
        ON CREATE SET x.repo = r.repo, x.number = r.number, x.title = r.title, x.summary = r.summary,
                      x.merged = r.merged, x.internal = r.internal, x.at = r.at, x.ts = r.ts
        WITH x, r WHERE r.internal MATCH (p:Product {name: r.product}) MERGE (x)-[:FOR_PRODUCT]->(p)"""),
    ("channel", """UNWIND $rows AS r MATCH (p:Product {name: r.product})
        CREATE (:Channel {id: r.id, name: r.name, kind: r.kind})-[:FOR_PRODUCT]->(p)"""),
    ("message", """UNWIND $rows AS r MATCH (c:Channel {id: r.channel})
        CREATE (m:Message {id: r.id, text: r.text, at: r.at, ts: r.ts})-[:IN]->(c)
        WITH m, r WHERE r.sender IS NOT NULL MATCH (a:Person {id: r.sender}) CREATE (a)-[:POSTED]->(m)"""),
    ("rename", "UNWIND $rows AS r MATCH (c:Channel {id: r.channel}), (m:Message {id: r.msg}) CREATE (c)-[:RENAMED {to: r.to, ts: r.ts}]->(m)"),
    ("mentions", "UNWIND $rows AS r MATCH (m:Message {id: r.msg}), (a:Person {id: r.person}) CREATE (m)-[:MENTIONS]->(a)"),
    ("mentions_customer", "UNWIND $rows AS r MATCH (m:Message {id: r.msg}), (c:Customer {id: r.customer}) CREATE (m)-[:MENTIONS]->(c)"),
    ("references_doc", "UNWIND $rows AS r MATCH (m:Message {id: r.msg}), (d:Document {id: r.doc}) CREATE (m)-[:REFERENCES]->(d)"),
    ("references_pr", "UNWIND $rows AS r MATCH (m:Message {id: r.msg}), (x:PullRequest {id: r.pr}) CREATE (m)-[:REFERENCES]->(x)"),
    ("opened_pr", "UNWIND $rows AS r MATCH (a:Person {id: r.person}), (x:PullRequest {id: r.pr}) MERGE (a)-[:OPENED]->(x)"),
    ("issue", """UNWIND $rows AS r MATCH (p:Product {name: r.product})
        CREATE (i:Issue {id: r.id, title: r.title, description: r.description, at: r.at, ts: r.ts, status: 'open'})
        MERGE (c:Component {id: r.product + '::' + coalesce(r.component, 'Unspecified')})
          ON CREATE SET c.name = coalesce(r.component, 'Unspecified')
        MERGE (c)-[:PART_OF]->(p)
        CREATE (i)-[:IN_COMPONENT]->(c)"""),
    ("issue_customer", "UNWIND $rows AS r MATCH (i:Issue {id: r.issue}), (c:Customer {id: r.customer}) MERGE (i)-[:AFFECTS]->(c)"),
    ("assigned", "UNWIND $rows AS r MATCH (i:Issue {id: r.issue}), (a:Person {id: r.person}) CREATE (i)-[:ASSIGNED_TO {ts: r.ts}]->(a)"),
    ("fixed_by", "UNWIND $rows AS r MATCH (i:Issue {id: r.issue}), (x:PullRequest {id: r.pr}) MERGE (i)-[:FIXED_BY]->(x)"),
    ("similar_to", "UNWIND $rows AS r MATCH (i:Issue {id: r.issue}), (x:PullRequest {id: r.pr}) MERGE (i)-[:SIMILAR_TO]->(x)"),
    ("root_cause", "UNWIND $rows AS r MATCH (i:Issue {id: r.issue}) SET i.root_cause = r.text, i.root_cause_msg = r.msg"),
]

EVENT_WRITES = {
    kind: f"UNWIND $rows AS r MATCH (i:Issue {{id: r.issue}}), (m:Message {{id: r.msg}}) CREATE (i)-[:{kind} {{match: coalesce(r.match, 'quoted-title')}}]->(m)"
    for kind in ("REPORTED_IN", "RESOLVED_IN", "REOPENED_IN")
}

# Derived state, recomputed from source edges after every load.
DERIVED = [
    # current status = the kind of the most recent lifecycle event
    """MATCH (i:Issue)-[e:REPORTED_IN|RESOLVED_IN|REOPENED_IN]->(m:Message)
       WITH i, e, m ORDER BY m.ts DESC
       WITH i, collect(type(e))[0] AS last
       SET i.status = CASE last WHEN 'RESOLVED_IN' THEN 'resolved' WHEN 'REOPENED_IN' THEN 'reopened' ELSE 'open' END""",
    # the fixer is whoever posted the resolution message
    """MATCH (i:Issue)-[:RESOLVED_IN]->(m:Message)<-[:POSTED]-(a:Person)
       MERGE (a)-[:RESOLVED]->(i)""",
    # people who worked an issue together (assignee, resolver) or sat in the same meeting collaborate
    """MATCH (a:Person)-[:ATTENDED]->(:Meeting)<-[:ATTENDED]-(b:Person) WHERE a.id < b.id
       WITH a, b, count(*) AS w MERGE (a)-[c:COLLABORATED_WITH]->(b) SET c.weight = w""",
    # the same kind of component in different products (e.g. every product's "API Gateway")
    """MATCH (a:Component), (b:Component) WHERE a.name = b.name AND a.id < b.id AND a.name <> 'Unspecified'
       MERGE (a)-[:SAME_KIND_AS]->(b)""",
]


def batches(rows, n=2000):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def load(root: Path, products=None, graph_name: str | None = None, reset: bool = True) -> dict:
    g = graph(graph_name)
    if reset:
        try:
            g.delete()
        except Exception:
            pass
    rows = parse(root, products)
    t0 = time.time()
    for q in INDEXES:
        g.query(q)
    for bucket, q in WRITES:
        for b in batches(rows.get(bucket, [])):
            g.query(q, {"rows": b})
    events = rows.get("issue_event", [])
    for kind, q in EVENT_WRITES.items():
        for b in batches([e for e in events if e["kind"] == kind]):
            g.query(q, {"rows": b})
    for q in DERIVED:
        g.query(q)
    out = {"seconds": round(time.time() - t0, 1), **rows["stats"]}
    derived = Path(__file__).resolve().parents[1] / "data-derived" / "decisions"
    if derived.exists():
        out["decisions"] = load_decisions(root, derived)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(load(Path(sys.argv[1]), sys.argv[2:] or None))
