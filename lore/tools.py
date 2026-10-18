"""The tool catalogue: every query the agent can run, written by hand.

The model never writes Cypher. It picks a tool and fills typed arguments; each
entity argument is resolved against the graph first, so an unknown or
ambiguous name stops the tool before any reasoning query runs. Every answer
carries an outcome, the rows, the graph paths behind them, the source
messages/documents, and a trace of the queries that produced it.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .db import graph

# -- outcomes ----------------------------------------------------------------
ANSWERED = "answered_with_path"
FROM_MEMORY = "answered_from_memory"
NOT_IN_GRAPH = "not_in_graph"
UNKNOWN = "unknown_entity"
AMBIGUOUS = "ambiguous_entity"
PROPOSED = "action_proposed"
APPLIED = "action_applied"

# A node reference as every path query returns it.
REF = "{id: coalesce(n.id, n.name), label: labels(n)[0], name: coalesce(n.company, n.name, n.title, n.summary, n.type, n.id)}"
PATH = f"[n IN nodes(p) | {REF}] AS path, [r IN relationships(p) | type(r)] AS rels"

EID = re.compile(r"^eid_[0-9a-f]{8}$")
CUST = re.compile(r"^CUST-\d{4}$", re.I)
FT_UNSAFE = re.compile(r"[^\w\s]")


@dataclass
class Answer:
    tool: str
    args: dict
    outcome: str = NOT_IN_GRAPH
    summary: str = ""
    rows: list = field(default_factory=list)
    paths: list = field(default_factory=list)      # [{path: [refs], rels: [types]}]
    sources: list = field(default_factory=list)    # [{id, kind, at, text}]
    candidates: list = field(default_factory=list)  # for unknown/ambiguous
    trace: list = field(default_factory=list)      # [{query, ms, rows, cypher}]

    def dict(self):
        return self.__dict__


class Stop(Exception):
    """Raised by a resolver: the answer is already final (unknown/ambiguous)."""

    def __init__(self, answer: Answer):
        self.answer = answer


def run(ans: Answer, name: str, cypher: str, params: dict | None = None, write: bool = False) -> list[dict]:
    g = graph()
    t = time.perf_counter()
    res = g.query(cypher, params or {}) if write else g.ro_query(cypher, params or {})
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in (res.header or [])]
    cols = [c.decode() if isinstance(c, bytes) else c for c in cols]
    rows = [dict(zip(cols, r)) for r in res.result_set]
    ans.trace.append({"query": name, "ms": round((time.perf_counter() - t) * 1000, 1), "rows": len(rows),
                      "cypher": " ".join(cypher.split())})
    return rows


def add_paths(ans: Answer, rows: list[dict], limit: int = 40):
    seen = {json.dumps(p["path"]) for p in ans.paths}
    for r in rows:
        if "path" in r and r["path"]:
            key = json.dumps(r["path"])
            if key not in seen and len(ans.paths) < limit:
                seen.add(key)
                ans.paths.append({"path": r["path"], "rels": r.get("rels", [])})


def add_source(ans: Answer, sid, kind, at, text):
    if sid and all(s["id"] != sid for s in ans.sources):
        ans.sources.append({"id": sid, "kind": kind, "at": at, "text": (text or "")[:600]})


def ft(text: str) -> str:
    """Full-text queries use RediSearch syntax; strip operators from user text."""
    return " ".join(FT_UNSAFE.sub(" ", text).split())


# -- resolvers ---------------------------------------------------------------
def _stop(ans, outcome, what, cands):
    ans.outcome, ans.candidates = outcome, cands
    if outcome == UNKNOWN:
        ans.summary = f"No {what} matching that name exists in the graph, so no query was run."
    else:
        ans.summary = f"{len(cands)} different {what}s match that name. Pick one by id; I won't guess."
    raise Stop(ans)


def resolve_person(ans: Answer, ref: str) -> dict:
    ref = ref.strip()
    if EID.match(ref):
        rows = run(ans, "resolve_person", "MATCH (p:Person {id: $id}) RETURN p.id AS id, p.name AS name, p.role AS role", {"id": ref})
    else:
        rows = run(ans, "resolve_person", """
            MATCH (p:Person) WHERE toLower(p.name) = toLower($name)
            OPTIONAL MATCH (p)-[:MEMBER_OF]->(prod:Product)
            RETURN p.id AS id, p.name AS name, p.role AS role, p.location AS location, collect(prod.name) AS products
            ORDER BY id""", {"name": ref})
        if not rows and ft(ref):
            rows = run(ans, "resolve_person_fuzzy", """
                CALL db.idx.fulltext.queryNodes('Person', $q) YIELD node, score
                RETURN node.id AS id, node.name AS name, node.role AS role, score ORDER BY score DESC LIMIT 5""", {"q": ft(ref)})
            if rows:
                _stop(ans, AMBIGUOUS if len(rows) > 1 else UNKNOWN, "person", rows)
    if not rows:
        _stop(ans, UNKNOWN, "person", [])
    if len(rows) > 1:
        _stop(ans, AMBIGUOUS, "person", rows)
    return rows[0]


def resolve_customer(ans: Answer, ref: str) -> dict:
    ref = ref.strip()
    if CUST.match(ref):
        rows = run(ans, "resolve_customer", "MATCH (c:Customer {id: $id}) RETURN c.id AS id, c.name AS name, c.company AS company", {"id": ref.upper()})
    else:
        rows = run(ans, "resolve_customer", """
            MATCH (c:Customer) WHERE toLower(c.company) = toLower($q) OR toLower(c.name) = toLower($q)
            RETURN c.id AS id, c.name AS name, c.company AS company, c.role AS role ORDER BY id""", {"q": ref})
    if not rows:
        _stop(ans, UNKNOWN, "customer", [])
    if len(rows) > 1:
        _stop(ans, AMBIGUOUS, "customer", rows)
    return rows[0]


def resolve_product(ans: Answer, ref: str) -> dict:
    rows = run(ans, "resolve_product", """
        MATCH (p:Product) WHERE toLower(p.name) = toLower($q) RETURN p.name AS id, p.name AS name""", {"q": ref.strip()})
    if not rows:  # maybe an old codename that only survives in channel names (planning-dAIX -> TrendForce)
        rows = run(ans, "resolve_product_codename", """
            MATCH (c:Channel)-[:FOR_PRODUCT]->(p:Product) WHERE toLower(c.name) ENDS WITH toLower('-' + $q)
            RETURN DISTINCT p.name AS id, p.name AS name, c.name AS via_channel""", {"q": ref.strip()})
    if not rows:
        _stop(ans, UNKNOWN, "product", [])
    return rows[0]


def resolve_component(ans: Answer, ref: str, product: str | None = None) -> dict:
    if "::" in ref:
        rows = run(ans, "resolve_component", "MATCH (c:Component {id: $id}) RETURN c.id AS id, c.name AS name", {"id": ref})
    else:
        rows = run(ans, "resolve_component", """
            CALL db.idx.fulltext.queryNodes('Component', $q) YIELD node, score
            MATCH (node)-[:PART_OF]->(p:Product) WHERE $product IS NULL OR toLower(p.name) = toLower($product)
            RETURN node.id AS id, node.name AS name, p.name AS product, score ORDER BY score DESC LIMIT 6""",
                   {"q": ft(ref), "product": product})
        exact = [r for r in rows if r["name"].lower() == ref.strip().lower()]
        rows = exact or rows
    if not rows:
        _stop(ans, UNKNOWN, "component", [])
    if len(rows) > 1:
        _stop(ans, AMBIGUOUS, "component", rows)
    return rows[0]


def resolve_issue(ans: Answer, ref: str) -> dict:
    if "::" in ref:
        rows = run(ans, "resolve_issue", "MATCH (i:Issue) WHERE toLower(i.id) = toLower($id) RETURN i.id AS id, i.title AS title", {"id": ref.strip()})
    else:
        rows = run(ans, "resolve_issue", """
            MATCH (i:Issue) WHERE toLower(i.title) = toLower($q) RETURN i.id AS id, i.title AS title""", {"q": ref.strip()})
        if not rows and ft(ref):
            rows = run(ans, "resolve_issue_fuzzy", """
                CALL db.idx.fulltext.queryNodes('Issue', $q) YIELD node, score
                RETURN node.id AS id, node.title AS title, score ORDER BY score DESC LIMIT 5""", {"q": ft(ref)})
            if len(rows) > 1:
                _stop(ans, AMBIGUOUS, "issue", rows)
    if not rows:
        _stop(ans, UNKNOWN, "issue", [])
    if len(rows) > 1:
        _stop(ans, AMBIGUOUS, "issue", rows)
    return rows[0]


RESOLVERS = {"person": resolve_person, "customer": resolve_customer, "product": resolve_product,
             "component": resolve_component, "issue": resolve_issue}


# -- tools -------------------------------------------------------------------
def find(query: str, kind: str = "any") -> Answer:
    """Look up entities by name. Use first when you are unsure what something is called."""
    ans = Answer("find", {"query": query, "kind": kind})
    q = ft(query)
    if not q:
        ans.outcome, ans.summary = UNKNOWN, "Empty query."
        return ans
    if len(q.split()[-1]) >= 2:
        q += "*"  # prefix match on the last word, so it works as you type
    labels = ["Person", "Customer", "Component", "Issue", "Decision"] if kind == "any" else [kind.capitalize()]
    for label in labels:
        rows = run(ans, f"find_{label}", f"""
            CALL db.idx.fulltext.queryNodes('{label}', $q) YIELD node AS n, score
            OPTIONAL MATCH (n)-[:MEMBER_OF|PART_OF|DECIDED_IN|IN_COMPONENT]->(ctx)
            OPTIONAL MATCH (ctx)-[:FOR_PRODUCT|PART_OF]->(ctx2:Product)
            WITH n, score, collect(DISTINCT coalesce(ctx2.name, ctx.name)) AS context
            RETURN {REF} AS ref, n.role AS role, n.company AS company, n.location AS location, n.status AS status,
                   context, score ORDER BY score DESC LIMIT 6""", {"q": q})
        ans.candidates += [{**r["ref"], "role": r["role"], "company": r["company"], "location": r["location"],
                            "status": r["status"], "context": [c for c in r["context"] if c][:6],
                            "score": round(r["score"], 2)} for r in rows]
    if kind in ("any", "product"):
        prods = run(ans, "find_Product", """
            MATCH (p:Product) WHERE toLower(p.name) CONTAINS toLower($q) RETURN p.name AS name""", {"q": query.strip()})
        ans.candidates = [{"id": r["name"], "label": "Product", "name": r["name"], "context": []} for r in prods] + ans.candidates
    ans.outcome = ANSWERED if ans.candidates else UNKNOWN
    ans.summary = f"{len(ans.candidates)} matching entities." if ans.candidates else "Nothing in the graph matches that name."
    return ans


def who_knows(component: str, product: str | None = None, depth: int = 2) -> Answer:
    """Rank the people who know a component, with the evidence path for each.
    depth 1: people who were assigned or resolved its issues.
    depth 2: + authors of the product's current design/spec documents.
    depth 3: + people who resolved issues in same-named components of other products."""
    depth = max(1, min(int(depth), 3))
    ans = Answer("who_knows", {"component": component, "product": product, "depth": depth})
    try:
        c = resolve_component(ans, component, product)
    except Stop as s:
        return s.answer
    evidence = [
        (1, 3.0, "resolved an issue in it", f"""
            MATCH p = (c:Component {{id: $cid}})<-[:IN_COMPONENT]-(i:Issue)<-[:RESOLVED]-(a:Person)
            RETURN a.id AS id, a.name AS name, a.role AS role, i.title AS item, {PATH}"""),
        (1, 1.0, "was assigned an issue in it", f"""
            MATCH p = (c:Component {{id: $cid}})<-[:IN_COMPONENT]-(i:Issue)-[:ASSIGNED_TO]->(a:Person)
            RETURN a.id AS id, a.name AS name, a.role AS role, i.title AS item, {PATH}"""),
        (2, 2.0, "wrote the current design/spec doc", f"""
            MATCH p = (c:Component {{id: $cid}})-[:PART_OF]->(:Product)<-[:DOCUMENTS]-(d:Document)-[:AUTHORED_BY]->(a:Person)
            WHERE d.type IN ['System Design Document', 'Technical Specifications Document']
            OPTIONAL MATCH (newer:Document)-[:SUPERSEDES]->(d)
            WITH a, d, p, newer WHERE newer IS NULL
            RETURN a.id AS id, a.name AS name, a.role AS role, d.id AS item, {PATH}"""),
        (3, 1.5, "fixed the same kind of component in another product", f"""
            MATCH p = (c:Component {{id: $cid}})-[:SAME_KIND_AS]-(c2:Component)<-[:IN_COMPONENT]-(i:Issue)<-[:RESOLVED]-(a:Person)
            RETURN a.id AS id, a.name AS name, a.role AS role, i.title AS item, {PATH}"""),
    ]
    people: dict[str, dict] = {}
    for d, w, why, q in evidence:
        if d > depth:
            continue
        rows = run(ans, f"who_knows_d{d}:{why}", q, {"cid": c["id"]})
        for r in rows:
            e = people.setdefault(r["id"], {"id": r["id"], "name": r["name"], "role": r["role"], "score": 0.0,
                                            "evidence": [], "hops": d})
            e["score"] += w
            e["hops"] = min(e["hops"], d)
            e["evidence"].append(f"{why}: {r['item']}")
        add_paths(ans, rows)
    ans.rows = sorted(people.values(), key=lambda e: (-e["score"], e["name"]))[:15]
    if not ans.rows:
        ans.outcome = NOT_IN_GRAPH
        ans.summary = (f"'{c['name']}' exists, but nothing in the graph links a person to it at depth {depth}. "
                       "That means the knowledge isn't recorded, not that nobody has it.")
    else:
        ans.outcome = ANSWERED
        top = ", ".join(f"{p['name']} ({p['id']})" for p in ans.rows[:3])
        ans.summary = f"{len(ans.rows)} people know '{c['name']}' ({c['id'].split('::')[0]}) at depth {depth}. Strongest: {top}."
    return ans


def if_person_leaves(person: str, depth: int = 2) -> Answer:
    """What is at risk if this person leaves: their open work, the documents only they
    own, the components where they are the only one who has fixed anything, the customers
    waiting on them, and (depth 3) who could take each piece over."""
    depth = max(1, min(int(depth), 3))
    ans = Answer("if_person_leaves", {"person": person, "depth": depth})
    try:
        p = resolve_person(ans, person)
    except Stop as s:
        return s.answer
    pid = p["id"]
    risks = []
    open_work = run(ans, "open_work", f"""
        MATCH p = (a:Person {{id: $pid}})<-[asg:ASSIGNED_TO]-(i:Issue)-[:IN_COMPONENT]->(c:Component)
        WHERE i.status <> 'resolved' AND asg.until IS NULL
        RETURN i.id AS id, i.title AS title, i.status AS status, c.id AS component, {PATH}""", {"pid": pid})
    add_paths(ans, open_work)
    risks += [{"kind": "open issue", "item": r["title"], "status": r["status"], "component": r["component"], "hops": 1}
              for r in open_work]
    docs = run(ans, "current_docs", f"""
        MATCH p = (a:Person {{id: $pid}})<-[:AUTHORED_BY]-(d:Document)-[:DOCUMENTS]->(prod:Product)
        OPTIONAL MATCH (newer:Document)-[:SUPERSEDES]->(d)
        WITH d, prod, p, newer WHERE newer IS NULL
        RETURN d.id AS id, d.type AS type, prod.name AS product, {PATH}""", {"pid": pid})
    add_paths(ans, docs)
    risks += [{"kind": "owns current document", "item": f"{r['type']} ({r['id']})", "product": r["product"], "hops": 1}
              for r in docs]
    commitments = run(ans, "open_commitments", f"""
        MATCH p = (a:Person {{id: $pid}})<-[:OWNED_BY]-(t:Decision {{kind: 'commitment'}})-[:DECIDED_IN]->(m:Meeting)
        OPTIONAL MATCH (later:Decision)-[:REVISES]->(t)
        WITH t, m, p, later WHERE later IS NULL
        RETURN t.id AS id, t.summary AS summary, m.id AS meeting, t.quote AS quote, m.at AS at, {PATH}""", {"pid": pid})
    add_paths(ans, commitments)
    for r in commitments:
        risks.append({"kind": "meeting commitment", "item": r["summary"], "hops": 1})
        add_source(ans, r["meeting"], "meeting", r["at"], r["quote"])
    if depth >= 2:
        sole = run(ans, "sole_expert", f"""
            MATCH (a:Person {{id: $pid}})-[:RESOLVED]->(:Issue)-[:IN_COMPONENT]->(c:Component)
            WHERE c.name <> 'Unspecified'
            WITH DISTINCT a, c
            MATCH (c)<-[:IN_COMPONENT]-(:Issue)<-[:RESOLVED]-(x:Person)
            WITH a, c, collect(DISTINCT x.id) AS fixers WHERE size(fixers) = 1
            MATCH p = (a)-[:RESOLVED]->(:Issue)-[:IN_COMPONENT]->(c)
            RETURN c.id AS id, c.name AS name, {PATH}""", {"pid": pid})
        add_paths(ans, sole)
        for r in {r["id"]: r for r in sole}.values():
            risks.append({"kind": "only person who has fixed this component", "item": r["id"], "hops": 2})
        custs = run(ans, "customers_waiting", f"""
            MATCH p = (a:Person {{id: $pid}})<-[asg:ASSIGNED_TO]-(i:Issue)-[:AFFECTS]->(cu:Customer)
            WHERE i.status <> 'resolved' AND asg.until IS NULL
            RETURN cu.id AS id, cu.company AS company, i.title AS issue, {PATH}""", {"pid": pid})
        add_paths(ans, custs)
        waiting: dict[str, dict] = {}
        for r in custs:  # one row per customer, listing every open issue they wait on
            w = waiting.setdefault(r["id"], {"kind": "customer waiting", "item": f"{r['company']} ({r['id']})",
                                             "via": [], "hops": 2})
            w["via"].append(r["issue"])
        risks += [{**w, "via": "; ".join(w["via"])} for w in waiting.values()]
    if depth >= 3:
        succ = run(ans, "successors", f"""
            MATCH (a:Person {{id: $pid}})<-[asg:ASSIGNED_TO]-(i:Issue)-[:IN_COMPONENT]->(c:Component)
            WHERE i.status <> 'resolved' AND asg.until IS NULL
            MATCH p = (a)<-[:ASSIGNED_TO]-(i)-[:IN_COMPONENT]->(c)-[:SAME_KIND_AS*0..1]-(c2:Component)<-[:IN_COMPONENT]-(:Issue)<-[:RESOLVED]-(s:Person)
            WHERE s.id <> $pid
            RETURN i.title AS issue, s.id AS id, s.name AS name, count(*) AS fixes, collect({PATH.split(' AS path')[0]})[0] AS path, collect([r IN relationships(p) | type(r)])[0] AS rels
            ORDER BY fixes DESC""", {"pid": pid})
        add_paths(ans, succ)
        seen = set()
        for r in succ:
            if r["issue"] not in seen:
                seen.add(r["issue"])
                risks.append({"kind": "possible successor", "item": f"{r['name']} ({r['id']})", "via": r["issue"], "hops": 3})
    ans.rows = risks
    if not risks:
        ans.outcome = NOT_IN_GRAPH
        ans.summary = f"{p['name']} ({pid}) is in the graph, but nothing open or uniquely theirs is recorded at depth {depth}."
    else:
        ans.outcome = ANSWERED
        kinds = {}
        for r in risks:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        ans.summary = f"If {p['name']} ({pid}, {p.get('role')}) leaves, at depth {depth}: " + "; ".join(f"{v} × {k}" for k, v in kinds.items()) + "."
    return ans


def issue_history(issue: str) -> Answer:
    """The full lifecycle of an issue, every step tied to the message that recorded it."""
    ans = Answer("issue_history", {"issue": issue})
    try:
        i = resolve_issue(ans, issue)
    except Stop as s:
        return s.answer
    info = run(ans, "issue", """
        MATCH (i:Issue {id: $id})-[:IN_COMPONENT]->(c:Component)-[:PART_OF]->(prod:Product)
        OPTIONAL MATCH (i)-[:AFFECTS]->(cu:Customer)
        OPTIONAL MATCH (i)-[:FIXED_BY]->(pr:PullRequest)
        OPTIONAL MATCH (i)-[:SIMILAR_TO]->(ext:PullRequest)
        RETURN i.title AS title, i.status AS status, i.description AS description, i.root_cause AS root_cause,
               c.name AS component, prod.name AS product, collect(DISTINCT cu.id) AS customers,
               collect(DISTINCT pr.id) AS fix_prs, collect(DISTINCT ext.id) AS similar_open_source""", {"id": i["id"]})[0]
    events = run(ans, "timeline", f"""
        MATCH p = (i:Issue {{id: $id}})-[e:REPORTED_IN|RESOLVED_IN|REOPENED_IN]->(m:Message)
        OPTIONAL MATCH (a:Person)-[:POSTED]->(m)
        OPTIONAL MATCH (i)-[asg:ASSIGNED_TO]->(who:Person) WHERE type(e) = 'REPORTED_IN'
        RETURN type(e) AS event, e.match AS matched_by, m.id AS msg, m.at AS at, m.text AS text,
               a.name AS by, a.id AS by_id, who.name AS assignee, who.id AS assignee_id, {PATH}
        ORDER BY m.ts""", {"id": i["id"]})
    add_paths(ans, events)
    for e in events:
        add_source(ans, e["msg"], "slack", e["at"], e["text"])
    ans.rows = [{**info, "timeline": [{k: e[k] for k in ("event", "at", "by", "by_id", "assignee", "assignee_id", "matched_by", "msg")} for e in events]}]
    ans.outcome = ANSWERED
    last = events[-1] if events else None
    ans.summary = (f"'{info['title']}' ({info['product']} / {info['component']}) is currently {info['status'].upper()}"
                   + (f"; the latest event is {last['event'].replace('_IN', '').lower()} on {last['at'][:10]} by {last['by']}" if last else "")
                   + (f". Root cause recorded: {info['root_cause'][:160]}" if info.get("root_cause") else "") + ".")
    return ans


def customer_impact(customer: str) -> Answer:
    """Everything recorded as affecting a customer, its current status, and who owns it."""
    ans = Answer("customer_impact", {"customer": customer})
    try:
        cu = resolve_customer(ans, customer)
    except Stop as s:
        return s.answer
    rows = run(ans, "customer_issues", f"""
        MATCH p = (cu:Customer {{id: $id}})<-[:AFFECTS]-(i:Issue)-[:IN_COMPONENT]->(c:Component)-[:PART_OF]->(prod:Product)
        OPTIONAL MATCH (i)-[cur:ASSIGNED_TO]->(a:Person) WHERE cur.until IS NULL
        OPTIONAL MATCH (i)-[:REPORTED_IN]->(m:Message)
        WITH i, prod, c, m, p, collect(DISTINCT a) AS owners
        RETURN i.id AS id, i.title AS title, i.status AS status, prod.name AS product, c.name AS component,
               owners[0].name AS owner, owners[0].id AS owner_id, m.id AS msg, m.at AS at, m.text AS text, {PATH}
        ORDER BY i.status DESC, m.ts DESC""", {"id": cu["id"]})
    rows = list({r["id"]: r for r in rows}.values())
    add_paths(ans, rows)
    for r in rows:
        add_source(ans, r["msg"], "slack", r["at"], r["text"])
    ans.rows = [{k: r[k] for k in ("id", "title", "status", "product", "component", "owner", "owner_id")} for r in rows]
    if not rows:
        ans.outcome = NOT_IN_GRAPH
        ans.summary = f"{cu['company']} ({cu['id']}) is a known customer, but no issue in the graph is recorded as affecting them."
    else:
        ans.outcome = ANSWERED
        unresolved = [r for r in rows if r["status"] != "resolved"]
        ans.summary = f"{cu['company']} ({cu['id']}) is affected by {len(rows)} issues; {len(unresolved)} are not resolved."
    return ans


def trace_decision(topic: str, product: str | None = None) -> Answer:
    """Find decisions/commitments about a topic and trace each to the meeting, speaker and
    verbatim quote it came from, including what it revised or was revised by."""
    ans = Answer("trace_decision", {"topic": topic, "product": product})
    q = "|".join(w for w in ft(topic).split() if len(w) > 2)
    rows = run(ans, "decisions", f"""
        CALL db.idx.fulltext.queryNodes('Decision', $q) YIELD node AS d, score
        MATCH p = (d)-[:DECIDED_IN]->(m:Meeting)-[:FOR_PRODUCT]->(prod:Product)
        WHERE $product IS NULL OR toLower(prod.name) = toLower($product)
        OPTIONAL MATCH (d)-[:SPOKEN_BY]->(sp:Person)
        OPTIONAL MATCH (d)-[:OWNED_BY]->(ow:Person)
        OPTIONAL MATCH (newer:Decision)-[:REVISES]->(d)
        OPTIONAL MATCH (d)-[:REVISES]->(older:Decision)
        RETURN d.id AS id, d.kind AS kind, d.summary AS summary, d.quote AS quote, d.topic AS topic, m.id AS meeting,
               m.at AS at, prod.name AS product, collect(DISTINCT sp.name + ' (' + sp.id + ')') AS speaker,
               collect(DISTINCT ow.name + ' (' + ow.id + ')') AS owner, newer.id AS revised_by, older.id AS revises,
               score, {PATH}
        ORDER BY score DESC LIMIT 8""", {"q": q or "_", "product": product}) if q else []
    add_paths(ans, rows)
    for r in rows:
        add_source(ans, r["meeting"], "meeting", r["at"], r["quote"])
    ans.rows = [{k: v for k, v in r.items() if k not in ("path", "rels")} for r in rows]
    if not rows:
        ans.outcome = NOT_IN_GRAPH
        ans.summary = "No recorded decision matches that topic. Decisions are only extracted for TrendForce, KnowledgeForce and ContextForce meetings."
    else:
        ans.outcome = ANSWERED
        cur = [r for r in rows if not r["revised_by"]]
        ans.summary = f"{len(rows)} matching decisions; {len(cur)} are current (not revised by a later meeting)."
    return ans


def doc_lineage(product: str) -> Answer:
    """Which version of each document is current, who wrote it, and what it superseded."""
    ans = Answer("doc_lineage", {"product": product})
    try:
        pr = resolve_product(ans, product)
    except Stop as s:
        return s.answer
    rows = run(ans, "docs", f"""
        MATCH (d:Document)-[:DOCUMENTS]->(:Product {{name: $p}})
        OPTIONAL MATCH (d)-[:AUTHORED_BY]->(a:Person)
        OPTIONAL MATCH (newer:Document)-[:SUPERSEDES]->(d)
        OPTIONAL MATCH (d)-[:SUPERSEDES]->(prev:Document)
        OPTIONAL MATCH p = (d)-[:SUPERSEDES*1..4]->(:Document)
        RETURN d.type AS type, d.id AS id, d.at AS at, a.name AS author, a.id AS author_id, newer IS NULL AS current,
               [prev.id] AS supersedes, {PATH}, length(p) AS chain
        ORDER BY type, at, chain DESC""", {"p": pr["id"]})
    add_paths(ans, [r for r in rows if r["current"]])
    seen, out = set(), []
    for r in rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        out.append({k: r[k] for k in ("type", "id", "at", "author", "author_id", "current", "supersedes")})
    ans.rows = out
    ans.outcome = ANSWERED if out else NOT_IN_GRAPH
    ans.summary = f"{pr['id']} has {len(out)} documents; {sum(r['current'] for r in out)} are the current versions."
    return ans


CONNECT_RELS = ("ASSIGNED_TO|RESOLVED|AFFECTS|IN_COMPONENT|PART_OF|MEMBER_OF|REPORTS_TO|AUTHORED_BY|DOCUMENTS|"
                "ATTENDED|HAS_CUSTOMER|OWNED_BY|SPOKEN_BY|DECIDED_IN|FIXED_BY|OPENED|SAME_KIND_AS")


def connect(a: str, b: str) -> Answer:
    """How are two things connected? Shortest path through people, issues, components,
    products, customers, documents, meetings and decisions."""
    ans = Answer("connect", {"a": a, "b": b})
    ids = []
    for ref in (a, b):
        exact = run(ans, "resolve_id", """
            MATCH (n) WHERE n.id = $id OR (n:Product AND n.name = $id)
            RETURN coalesce(n.id, n.name) AS id LIMIT 1""", {"id": ref.strip()})
        if exact:
            ids.append(exact[0]["id"])
            continue
        try:
            if EID.match(ref.strip()):
                ids.append(resolve_person(ans, ref)["id"])
            elif CUST.match(ref.strip()):
                ids.append(resolve_customer(ans, ref)["id"])
            else:
                f = find(ref)
                ans.trace += f.trace
                exact = [c for c in f.candidates if str(c["name"]).lower() == ref.strip().lower() or c["id"] == ref.strip()]
                if len(exact) != 1:
                    ans.outcome = AMBIGUOUS if len(exact) > 1 or f.candidates else UNKNOWN
                    ans.candidates = exact or f.candidates
                    ans.summary = f"'{ref}' does not name exactly one thing in the graph; pick one by id."
                    return ans
                ids.append(exact[0]["id"])
        except Stop as s:
            return s.answer
    # FalkorDB's shortestPath() is directed-only, so use the SPpaths algorithm in both directions.
    rows = run(ans, "shortest_path", f"""
        MATCH (x) WHERE x.id = $a OR (x:Product AND x.name = $a)
        MATCH (y) WHERE y.id = $b OR (y:Product AND y.name = $b)
        CALL algo.SPpaths({{sourceNode: x, targetNode: y, relTypes: {CONNECT_RELS.split('|')!r},
                            relDirection: 'both', pathCount: 1, maxLen: 8}})
        YIELD path AS p
        RETURN {PATH}, length(p) AS hops""", {"a": ids[0], "b": ids[1]})
    add_paths(ans, rows)
    if not rows:
        ans.outcome = NOT_IN_GRAPH
        ans.summary = "Both exist, but no path of up to 8 recorded relationships connects them."
    else:
        ans.outcome = ANSWERED
        r = rows[0]
        chain = " → ".join(f"{n['name']}" + (f" -[{r['rels'][k]}]-" if k < len(r['rels']) else "") for k, n in enumerate(r["path"]))
        ans.rows = [{"hops": r["hops"], "chain": chain}]
        ans.summary = f"Connected in {r['hops']} hops: {chain}"
    return ans


def key_people(product: str, limit: int = 8) -> Answer:
    """The most load-bearing people on a product: PageRank and betweenness over who
    collaborates with whom (shared meetings), restricted to the product team."""
    limit = max(1, min(int(limit), 20))
    ans = Answer("key_people", {"product": product, "limit": limit})
    try:
        pr = resolve_product(ans, product)
    except Stop as s:
        return s.answer
    pagerank = run(ans, "pagerank", """
        CALL algo.pageRank('Person', 'COLLABORATED_WITH') YIELD node, score
        MATCH (node)-[:MEMBER_OF]->(:Product {name: $p})
        RETURN node.id AS id, node.name AS name, node.role AS role, score ORDER BY score DESC LIMIT $limit""",
                   {"p": pr["id"], "limit": limit})
    between = run(ans, "betweenness", """
        CALL algo.betweenness({nodeLabels: ['Person'], relationshipTypes: ['COLLABORATED_WITH']}) YIELD node, score
        MATCH (node)-[:MEMBER_OF]->(:Product {name: $p})
        RETURN node.id AS id, score ORDER BY score DESC""", {"p": pr["id"]})
    b = {r["id"]: r["score"] for r in between}
    ans.rows = [{**r, "pagerank": round(r.pop("score"), 5), "betweenness": round(b.get(r["id"], 0.0), 2)} for r in pagerank]
    ans.outcome = ANSWERED if ans.rows else NOT_IN_GRAPH
    ans.summary = (f"Most central people on {pr['id']} by PageRank over the meeting-collaboration graph: "
                   + ", ".join(f"{r['name']} ({r['id']})" for r in ans.rows[:3]) + ".") if ans.rows else "No collaboration data."
    return ans


def remember(entity_id: str, text: str, session: str = "default") -> Answer:
    """Write a finding back to the graph, attached to the entity it is about."""
    ans = Answer("remember", {"entity_id": entity_id, "text": text, "session": session})
    now = datetime.now(timezone.utc)
    rows = run(ans, "remember", f"""
        MATCH (n {{id: $id}})
        CREATE (f:Finding {{id: $fid, text: $text, ts: $ts, at: $at, session: $session}})-[:ABOUT]->(n)
        RETURN labels(n)[0] AS label""", {"id": entity_id, "fid": "finding_" + uuid.uuid4().hex[:8], "text": text[:1000], "ts": int(now.timestamp() * 1000),
                                          "at": now.isoformat(timespec="seconds"), "session": session}, write=True)
    ans.outcome = APPLIED if rows else UNKNOWN
    ans.summary = f"Saved a finding about {rows[0]['label']} {entity_id}." if rows else f"No entity with id {entity_id}; nothing saved."
    return ans


def recall(entity_id: str) -> Answer:
    """Findings earlier sessions saved about an entity, newest first, with dates."""
    ans = Answer("recall", {"entity_id": entity_id})
    rows = run(ans, "recall", f"""
        MATCH p = (f:Finding)-[:ABOUT]->(n {{id: $id}})
        RETURN f.text AS finding, f.at AS at, f.session AS session, {PATH} ORDER BY f.ts DESC LIMIT 5""", {"id": entity_id})
    add_paths(ans, rows)
    ans.rows = [{k: r[k] for k in ("finding", "at", "session")} for r in rows]
    ans.outcome = FROM_MEMORY if rows else NOT_IN_GRAPH
    ans.summary = (f"{len(rows)} earlier findings; latest from {rows[0]['at']}." if rows else "No earlier findings about this.")
    return ans


def plan_handoff(person: str) -> Answer:
    """Draft (do not apply) a handoff of a person's unresolved issues. Each issue gets the
    colleague with the strongest graph evidence on that component. Returns an action id;
    nothing changes until a human approves it with apply_action."""
    ans = Answer("plan_handoff", {"person": person})
    try:
        p = resolve_person(ans, person)
    except Stop as s:
        return s.answer
    rows = run(ans, "handoff_candidates", f"""
        MATCH (a:Person {{id: $pid}})<-[asg:ASSIGNED_TO]-(i:Issue)-[:IN_COMPONENT]->(c:Component)-[:PART_OF]->(prod:Product)
        WHERE i.status <> 'resolved' AND asg.until IS NULL
        OPTIONAL MATCH p = (a)<-[:ASSIGNED_TO]-(i)-[:IN_COMPONENT]->(c)-[:SAME_KIND_AS*0..1]-(:Component)<-[:IN_COMPONENT]-(:Issue)<-[:RESOLVED]-(s:Person)
        WHERE s.id <> $pid
        WITH i, prod, s, count(p) AS fixes, collect(p)[0] AS p
        OPTIONAL MATCH (s)-[team:MEMBER_OF]->(prod)
        RETURN i.id AS issue, i.title AS title, s.id AS sid, s.name AS sname, fixes, team IS NOT NULL AS same_team,
               CASE WHEN p IS NULL THEN [] ELSE [n IN nodes(p) | {REF}] END AS path,
               CASE WHEN p IS NULL THEN [] ELSE [r IN relationships(p) | type(r)] END AS rels
        ORDER BY issue, same_team DESC, fixes DESC""", {"pid": p["id"]})
    plan, seen = [], set()
    for r in rows:
        if r["issue"] in seen:
            continue
        seen.add(r["issue"])
        plan.append({"issue": r["issue"], "title": r["title"], "to": r["sid"], "to_name": r["sname"],
                     "evidence": f"resolved {r['fixes']} issue(s) in this or a same-kind component"
                                 + (" and is on the product team" if r["same_team"] else "") if r["sid"] else "no one with recorded experience: needs a human pick"})
        if r["path"]:
            ans.paths.append({"path": r["path"], "rels": r["rels"]})
    if not plan:
        ans.outcome = NOT_IN_GRAPH
        ans.summary = f"{p['name']} ({p['id']}) has no unresolved issues recorded; nothing to hand off."
        return ans
    action_id = "act_" + uuid.uuid4().hex[:8]
    run(ans, "store_proposal", """
        MATCH (a:Person {id: $pid})
        CREATE (x:Action {id: $id, kind: 'handoff', status: 'proposed', plan: $plan, ts: $ts})-[:CONCERNS]->(a)""",
        {"pid": p["id"], "id": action_id, "plan": json.dumps(plan), "ts": int(time.time() * 1000)}, write=True)
    ans.rows = plan
    ans.outcome = PROPOSED
    ans.summary = (f"Drafted handoff {action_id} for {len(plan)} unresolved issue(s) of {p['name']} ({p['id']}). "
                   "Nothing has changed yet; a human must approve it.")
    return ans


def apply_action(action_id: str, approved_by: str) -> Answer:
    """Apply a drafted action after a human approved it. Writes the new assignments with
    provenance (which action, who approved, when) and remembers the handoff."""
    ans = Answer("apply_action", {"action_id": action_id, "approved_by": approved_by})
    rows = run(ans, "load_action", "MATCH (x:Action {id: $id}) RETURN x.status AS status, x.plan AS plan", {"id": action_id})
    if not rows:
        ans.outcome, ans.summary = UNKNOWN, f"No action {action_id}."
        return ans
    if rows[0]["status"] != "proposed":
        ans.outcome, ans.summary = NOT_IN_GRAPH, f"Action {action_id} is already {rows[0]['status']}."
        return ans
    plan = [s for s in json.loads(rows[0]["plan"]) if s.get("to")]
    ts = int(time.time() * 1000)
    # Old assignments are closed, not deleted: history stays queryable, "current" is the open edge.
    run(ans, "end_old_assignments", """
        UNWIND $steps AS s
        MATCH (i:Issue {id: s.issue})-[old:ASSIGNED_TO]->(prev:Person)
        WHERE old.until IS NULL AND prev.id <> s.to
        SET old.until = $ts, old.ended_by = $id""", {"steps": plan, "id": action_id, "ts": ts}, write=True)
    run(ans, "apply_handoff", """
        UNWIND $steps AS s
        MATCH (i:Issue {id: s.issue}), (to:Person {id: s.to}), (x:Action {id: $id})
        CREATE (i)-[:ASSIGNED_TO {ts: $ts, via: $id, approved_by: $by}]->(to)
        CREATE (x)-[:REASSIGNED]->(i)
        SET x.status = 'applied', x.approved_by = $by, x.applied_ts = $ts""",
        {"steps": plan, "id": action_id, "ts": ts, "by": approved_by}, write=True)
    ans.rows = plan
    ans.outcome = APPLIED
    ans.summary = f"Applied {action_id}: {len(plan)} issue(s) reassigned, approved by {approved_by}."
    return ans


def revert_action(action_id: str) -> Answer:
    """Undo an applied action: remove the assignments it created and reopen the ones it
    closed. The Action node stays, marked reverted, so the history is kept."""
    ans = Answer("revert_action", {"action_id": action_id})
    rows = run(ans, "load_action", "MATCH (x:Action {id: $id}) RETURN x.status AS status", {"id": action_id})
    if not rows:
        ans.outcome, ans.summary = UNKNOWN, f"No action {action_id}."
        return ans
    if rows[0]["status"] != "applied":
        ans.outcome, ans.summary = NOT_IN_GRAPH, f"Action {action_id} is {rows[0]['status']}, so there is nothing to undo."
        return ans
    created = run(ans, "remove_new_assignments", """
        MATCH (:Issue)-[r:ASSIGNED_TO]->(:Person) WHERE r.via = $id
        DELETE r RETURN count(r) AS n""", {"id": action_id}, write=True)
    run(ans, "reopen_old_assignments", """
        MATCH (:Issue)-[r:ASSIGNED_TO]->(:Person) WHERE r.ended_by = $id
        SET r.until = NULL, r.ended_by = NULL""", {"id": action_id}, write=True)
    run(ans, "mark_reverted", """
        MATCH (x:Action {id: $id}) OPTIONAL MATCH (x)-[rr:REASSIGNED]->() DELETE rr
        SET x.status = 'reverted', x.reverted_ts = $ts""", {"id": action_id, "ts": int(time.time() * 1000)}, write=True)
    n = created[0]["n"] if created else 0
    ans.outcome = APPLIED
    ans.summary = f"Undid {action_id}: {n} reassignment(s) removed and the previous owners restored."
    return ans


HIDDEN_PROPS = {"content", "transcript", "text", "plan"}


def profile(entity_id: str) -> Answer:
    """One entity's facts and its neighbourhood: every relationship type with counts and
    a few examples. Use it to see what is connected to something before asking more."""
    ans = Answer("profile", {"entity_id": entity_id})
    head = run(ans, "entity", """
        MATCH (n) WHERE n.id = $id OR (n:Product AND n.name = $id)
        RETURN labels(n)[0] AS label, properties(n) AS props LIMIT 1""", {"id": entity_id})
    if not head:
        ans.outcome, ans.summary = UNKNOWN, f"Nothing in the graph has the id {entity_id}."
        return ans
    label, props = head[0]["label"], head[0]["props"]
    props = {k: v for k, v in props.items() if k not in HIDDEN_PROPS and k != "ts"}
    hood = run(ans, "neighbourhood", f"""
        MATCH (n) WHERE n.id = $id OR (n:Product AND n.name = $id)
        MATCH (n)-[r]-(m)
        WITH type(r) AS rel, startNode(r) = n AS outgoing, labels(m)[0] AS other, m
        WITH rel, outgoing, other, count(m) AS n, collect(m)[..12] AS sample
        RETURN rel, outgoing, other, n,
               [x IN sample | {{id: coalesce(x.id, x.name), label: labels(x)[0],
                               name: coalesce(x.company, x.name, x.title, x.summary, x.type, x.id),
                               role: x.role, status: x.status, at: x.at}}] AS sample
        ORDER BY n DESC""", {"id": entity_id})
    ans.rows = [{"label": label, "props": props, "links": hood}]
    ans.outcome = ANSWERED
    name = props.get("company") or props.get("name") or props.get("title") or props.get("summary") or entity_id
    ans.summary = f"{label} {name}: {sum(h['n'] for h in hood)} connections across {len(hood)} relationship types."
    return ans


def decisions(product: str) -> Answer:
    """Every recorded decision and commitment for a product, oldest first, marking which
    are still current and which a later meeting revised."""
    ans = Answer("decisions", {"product": product})
    try:
        pr = resolve_product(ans, product)
    except Stop as s:
        return s.answer
    rows = run(ans, "decisions", """
        MATCH (d:Decision)-[:DECIDED_IN]->(m:Meeting)-[:FOR_PRODUCT]->(:Product {name: $p})
        OPTIONAL MATCH (newer:Decision)-[:REVISES]->(d)
        OPTIONAL MATCH (d)-[:OWNED_BY]->(o:Person)
        RETURN d.id AS id, d.kind AS kind, d.summary AS summary, d.topic AS topic, d.quote AS quote, m.id AS meeting,
               m.at AS at, newer.id AS revised_by, collect(DISTINCT o.name + ' (' + o.id + ')') AS owner
        ORDER BY at, id""", {"p": pr["id"]})
    for r in rows:
        add_source(ans, r["meeting"], "meeting", r["at"], r["quote"])
    ans.rows = rows
    if not rows:
        ans.outcome = NOT_IN_GRAPH
        ans.summary = f"No decisions are recorded for {pr['id']}. They were extracted for TrendForce, KnowledgeForce and ContextForce only."
    else:
        ans.outcome = ANSWERED
        ans.summary = f"{len(rows)} decisions and commitments for {pr['id']}; {sum(1 for r in rows if not r['revised_by'])} are still current."
    return ans


TOOLS = {f.__name__: f for f in (find, profile, who_knows, if_person_leaves, issue_history, customer_impact,
                                 trace_decision, decisions, doc_lineage, connect, key_people, remember, recall,
                                 plan_handoff, apply_action, revert_action)}


def call(name: str, args: dict) -> dict:
    if name not in TOOLS:
        return Answer(name, args, outcome=UNKNOWN, summary=f"No tool named {name}.").dict()
    try:
        return TOOLS[name](**args).dict()
    except TypeError as e:
        return Answer(name, args, outcome=UNKNOWN, summary=f"Bad arguments: {e}").dict()
    except Exception as e:  # a query failure is reported, never papered over
        return Answer(name, args, outcome="error", summary=f"Query failed: {e}").dict()
