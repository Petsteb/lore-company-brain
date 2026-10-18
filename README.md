# Lore: a company brain that shows its work

**Graph Hacks 2026 (WeMakeDevs × FalkorDB), Track 03: Company Brain.**

Lore puts a company's people, teams, products, components, issues, customers, documents, meetings and decisions into one FalkorDB graph. An agent answers questions like *"what is at risk if Alice leaves?"*, *"who actually knows this component?"* and *"why was this decided, and is it still current?"* Every answer comes back with the **graph path** behind it, the **source messages** it rests on, and the **exact Cypher** that ran.

- **Live demo:** _see the submission page (Hugging Face Space)_
- **Demo video:** _see the submission page_
- **MCP endpoint:** `/mcp` on the deployment (streamable HTTP). Any MCP client can act as the agent.

---

## Why this needs a graph

The questions a company brain gets asked are not stored in any one record:

| Question | What has to be followed |
|---|---|
| If Alice leaves, what breaks? | Person ← assigned Issue → Component → other Issues ← who resolved them; Person ← open Issue → Customer |
| Who knows the API Authentication Layer? | Component ← Issue ← resolver; Component → Product ← current design doc → author; Component ~ same component in other products ← resolver |
| Is this bug fixed? | Issue → every lifecycle message, in time order. The *latest* event decides (it was resolved, then the customer reopened it) |
| Who owns this decision now? | Decision → Meeting, speaker, owner; Decision ← later Decision that `REVISES` it |

If you take FalkorDB out, none of these can be answered. The product is the traversals.

## The hero demo (also in the UI's "Demo scenarios")

1. **"Who is affected if Alice Brown leaves?"** returns `ambiguous_entity`. Four employees are named Alice Brown (a VP of Engineering, two software engineers and a QA specialist). Lore lists them with role and products and **refuses to guess**.
2. Pick the engineer `eid_92c62291`:
   - **Depth 1:** 7 open issues and 4 meeting commitments, each with its source.
   - **Depth 2** adds the 6 customers waiting on her open issues, and any component where she is the *only* person who has ever fixed anything (none in her case; the check runs).
   - **Depth 3** adds **who could take over each piece** (7 successors). Successors are found through `SAME_KIND_AS` components in other products: someone on another team fixed the same kind of thing. A join does not find that.
3. **"Draft a handoff"** creates a *proposed* `Action` node (nothing changes). **Approve** writes new `ASSIGNED_TO` edges carrying `via` and `approved_by`, and closes the old ones with `until` instead of deleting them. Re-run "if Alice leaves": 7 open issues become 0. Her 4 meeting commitments remain, because commitments have to be reassigned by people. Any issue with no experienced candidate would stay flagged as "needs a human pick".
4. **"Quantum Flux Capacitor"** returns `unknown_entity`, and no query runs at all.

## Architecture

```
HERB dataset (Slack, docs, meetings, PRs, org chart)            Claude (offline, build time)
        │ deterministic parse: IDs, @mentions, links,                  │ decisions/commitments from
        │ fixed bug-triage phrasing (lore/herb.py)                     │ meeting transcripts, each with
        ▼                                                              ▼ a VERBATIM quote
   FalkorDB graph  ◄──── loader: UNWIND batches, indexes, derived edges ── quote check rejects anything
        ▲                (lore/load.py, lore/decisions.py)                not found in the source
        │ 15 hand-written, parameterised Cypher tools (lore/tools.py)
        │   names resolved against the graph first; outcome + rows + paths + sources + trace
        ├── MCP server (lore/mcp_server.py): stdio, or HTTP at /mcp → any MCP client is the agent
        ├── REST API + tool console UI (lore/api.py, web/index.html)
        └── optional Claude agent (/api/ask, needs ANTHROPIC_API_KEY): picks tools, never writes Cypher;
            a reply naming an id that no tool returned is rejected
```

One Docker image runs FalkorDB `v4.22.1` (pinned) and the app. The graph (about 40k nodes, 108k relationships) is rebuilt from the dataset at startup in about 5 seconds.

## Graph data model

**Nodes**

| Label | Count | Key properties |
|---|---|---|
| `Person` | 530 | `id` (eid), `name`, `role`, `location`, `org` |
| `Product` | 30 | `name` |
| `Component` | 198 | `id` = `product::name`, `name` |
| `Issue` | 745 | `id`, `title`, `description`, `status` (derived), `root_cause`, `ts` |
| `Customer` | 120 | `id` (CUST-…), `name`, `company` |
| `Document` | 400 | `id`, `type`, `at`, `content` |
| `Meeting` | 321 | `id`, `type`, `at`, `transcript` |
| `Decision` | 89 | `kind` (decision/commitment), `summary`, `quote` (verbatim), `topic` |
| `Message` | 33,632 | `id`, `text`, `at`, `ts` (Slack) |
| `PullRequest` | 3,562 | `id` (url), `title`, `merged`, `internal` |
| `Channel` | 302 | `name`, `kind` (planning/develop/bug) |
| `Finding`, `Action` | runtime | memory written by the agent; drafted and applied actions |

**Relationships** (counts after load)

```
(:Person)-[:REPORTS_TO]->(:Person)                         512   org chart
(:Person)-[:MEMBER_OF]->(:Product)                        1370
(:Product)-[:HAS_CUSTOMER]->(:Customer)                    720
(:Component)-[:PART_OF]->(:Product)                        198
(:Component)-[:SAME_KIND_AS]->(:Component)                 584   same component name in different products
(:Issue)-[:IN_COMPONENT]->(:Component)                     745
(:Issue)-[:AFFECTS]->(:Customer)                           748
(:Issue)-[:ASSIGNED_TO {ts, until?, via?, approved_by?}]->(:Person)   748   current = no `until`
(:Issue)-[:REPORTED_IN|RESOLVED_IN|REOPENED_IN {match}]->(:Message)   748/420/63   lifecycle, tied to the source message
(:Person)-[:RESOLVED]->(:Issue)                            408   derived: who posted the resolution
(:Issue)-[:FIXED_BY]->(:PullRequest)                       376
(:Issue)-[:SIMILAR_TO]->(:PullRequest)                     288   open-source PRs the team compared against
(:Document)-[:AUTHORED_BY]->(:Person) / -[:DOCUMENTS]->(:Product)   400 each
(:Document)-[:SUPERSEDES]->(:Document)                     250   version chain per document type
(:Person)-[:ATTENDED]->(:Meeting)-[:FOR_PRODUCT]->(:Product)   8884
(:Person)-[:COLLABORATED_WITH {weight}]->(:Person)        10292   derived from shared meetings (input to PageRank and betweenness)
(:Decision)-[:DECIDED_IN]->(:Meeting)                       89
(:Decision)-[:SPOKEN_BY|OWNED_BY {ambiguous}]->(:Person)   107/88
(:Decision)-[:REVISES]->(:Decision)                         22   later meeting changes an earlier decision
(:Person)-[:POSTED]->(:Message)-[:IN]->(:Channel)          32k/33k
(:Message)-[:MENTIONS]->(:Person|:Customer)               9867
(:Message)-[:REFERENCES]->(:Document|:PullRequest)        1615
(:Finding)-[:ABOUT]->(any)   (:Action)-[:CONCERNS|REASSIGNED]->(…)   runtime writes
```

### Keeping knowledge current

Nothing is overwritten; "current" is always derived from the history:
- **Issue status** comes from the latest lifecycle event. *Resolved* on 07-13, then *reopened* by the customer on 07-28, gives `reopened`.
- **Documents:** the current version is the one nothing `SUPERSEDES`.
- **Decisions:** a decision is current unless a later one `REVISES` it.
- **Assignments:** a handoff closes the old edge (`until`, `ended_by`) and adds a new one (`via`, `approved_by`).

### Provenance and honesty about the data
- Graph edges come from deterministic parsing of IDs, `@eid_…` mentions, `CUST-…` ids, links and HERB's fixed bug-triage phrasing.
- **Resolution messages:** 191 name the issue in quotes. 229 paraphrase it and were matched to the sender's own open issues: 84 because the sender had exactly one open issue in that channel, 145 by word overlap. Every edge records how it was matched (`match` property). **70 resolutions and 13 reopens could not be matched and are left out** rather than guessed.
- A quote mark only opens an issue title if it doesn't touch a letter, so apostrophes ("customer's") don't. An early version got this wrong and produced 36 junk titles; we found and fixed it during the build.
- **Decisions** (3 products, 89 items) were extracted by Claude at build time. Each has a verbatim `quote`, and the loader rejects any item whose quote isn't in the transcript (0 were rejected).
- **Speaker names** are resolved only among that meeting's participants. 60 links are to people who share a name, and those are marked `ambiguous: true` instead of being guessed.

## The tool catalogue (all Cypher is hand-written; the model only picks tools and fills arguments)

| Tool | What it answers | Graph work |
|---|---|---|
| `find` | what is this called? (search as you type) | full-text indexes (prefix match) on Person, Issue, Component, Customer, Decision, with each hit's product context so same-named people can be told apart |
| `profile(entity_id)` | what is this connected to? | one entity's facts plus every relationship type around it, with counts and examples |
| `if_person_leaves(person, depth)` | knowledge risk | open work → sole-expert components → waiting customers → successors via `SAME_KIND_AS` |
| `who_knows(component, product, depth)` | expertise, ranked with evidence | resolvers, assignees, current design-doc authors, cross-product resolvers |
| `issue_history(issue)` | lifecycle and current status | `REPORTED_IN`/`RESOLVED_IN`/`REOPENED_IN` → messages, fix PR, root cause |
| `customer_impact(customer)` | what is open for a customer | Customer ← Issue → Component → Product, current owner |
| `trace_decision(topic, product)` | why, who, from where, still current? | Decision → Meeting, speaker, owner, `REVISES` both ways |
| `decisions(product)` | what was decided, and what still stands | Decision → Meeting, with `REVISES` marking what's no longer current |
| `doc_lineage(product)` | which docs are current | `SUPERSEDES*1..4` chains |
| `connect(a, b)` | how are two things related | `algo.SPpaths`, undirected |
| `key_people(product)` | load-bearing people | `algo.pageRank` and `algo.betweenness` over `COLLABORATED_WITH` |
| `remember` / `recall` | memory across sessions | `Finding` nodes `ABOUT` an entity, returned with dates |
| `plan_handoff` / `apply_action` | get work done, with a human in the loop | draft `Action` node; approval writes reassignments with provenance |

Every call returns one of: `answered_with_path`, `answered_from_memory`, `not_in_graph` (the entity exists but nothing connects), `unknown_entity` (no query runs), `ambiguous_entity` (candidates are listed, no guess), `action_proposed`, `action_applied`.

### Key queries the product depends on

The depth parameter picks which of these run. FalkorDB doesn't accept a parameter as a variable-length bound, so depth is never put into the query text.

```cypher
// if_person_leaves, depth 1: open work (current assignments only)
MATCH p = (a:Person {id: $pid})<-[asg:ASSIGNED_TO]-(i:Issue)-[:IN_COMPONENT]->(c:Component)
WHERE i.status <> 'resolved' AND asg.until IS NULL
RETURN i.id, i.title, i.status, c.id, [n IN nodes(p) | {id: n.id, label: labels(n)[0]}] AS path

// depth 2: components where this person is the only one who has ever fixed anything
MATCH (a:Person {id: $pid})-[:RESOLVED]->(:Issue)-[:IN_COMPONENT]->(c:Component)
WITH DISTINCT a, c
MATCH (c)<-[:IN_COMPONENT]-(:Issue)<-[:RESOLVED]-(x:Person)
WITH a, c, collect(DISTINCT x.id) AS fixers WHERE size(fixers) = 1
MATCH p = (a)-[:RESOLVED]->(:Issue)-[:IN_COMPONENT]->(c)
RETURN c.id, c.name, nodes(p)

// depth 3: who could take over: resolvers of the same component, or of the same kind of component elsewhere
MATCH (a:Person {id: $pid})<-[asg:ASSIGNED_TO]-(i:Issue)-[:IN_COMPONENT]->(c:Component)
WHERE i.status <> 'resolved' AND asg.until IS NULL
MATCH p = (a)<-[:ASSIGNED_TO]-(i)-[:IN_COMPONENT]->(c)-[:SAME_KIND_AS*0..1]-(c2:Component)
          <-[:IN_COMPONENT]-(:Issue)<-[:RESOLVED]-(s:Person)
WHERE s.id <> $pid
RETURN i.title, s.id, s.name, count(*) AS fixes ORDER BY fixes DESC

// issue status is derived from the latest lifecycle event (run at load time)
MATCH (i:Issue)-[e:REPORTED_IN|RESOLVED_IN|REOPENED_IN]->(m:Message)
WITH i, e, m ORDER BY m.ts DESC
WITH i, collect(type(e))[0] AS last
SET i.status = CASE last WHEN 'RESOLVED_IN' THEN 'resolved' WHEN 'REOPENED_IN' THEN 'reopened' ELSE 'open' END

// trace_decision: full-text, then provenance and revision links in both directions
CALL db.idx.fulltext.queryNodes('Decision', $q) YIELD node AS d, score
MATCH p = (d)-[:DECIDED_IN]->(m:Meeting)-[:FOR_PRODUCT]->(prod:Product)
OPTIONAL MATCH (d)-[:SPOKEN_BY]->(sp:Person)
OPTIONAL MATCH (newer:Decision)-[:REVISES]->(d)
OPTIONAL MATCH (d)-[:REVISES]->(older:Decision)
RETURN d.summary, d.quote, m.id, m.at, sp.name, newer.id AS revised_by, older.id AS revises

// connect: FalkorDB's shortestPath() is directed-only, so use the algorithm in both directions
MATCH (x {id: $a}), (y {id: $b})
CALL algo.SPpaths({sourceNode: x, targetNode: y, relTypes: [...], relDirection: 'both', pathCount: 1, maxLen: 8})
YIELD path RETURN nodes(path), relationships(path)

// key_people: centrality over the derived collaboration graph
CALL algo.pageRank('Person', 'COLLABORATED_WITH') YIELD node, score
MATCH (node)-[:MEMBER_OF]->(:Product {name: $p}) RETURN node.name, score ORDER BY score DESC LIMIT $limit
CALL algo.betweenness({nodeLabels: ['Person'], relationshipTypes: ['COLLABORATED_WITH']}) YIELD node, score

// apply_action: close old assignments, add new ones with provenance
UNWIND $steps AS s
MATCH (i:Issue {id: s.issue})-[old:ASSIGNED_TO]->(prev:Person) WHERE old.until IS NULL AND prev.id <> s.to
SET old.until = $ts, old.ended_by = $id
```

All the queries, verbatim, are in [`lore/tools.py`](lore/tools.py) (tools), [`lore/load.py`](lore/load.py) (ingest and derived edges) and [`lore/decisions.py`](lore/decisions.py). The UI's trace panel also shows the exact Cypher of every call.

## Checked against HERB's own answers

HERB ships ground-truth answers. `eval/herb_eval.py` runs a fixed Cypher query for every "person" question whose answer is a pure graph pattern and checks for an exact match on the set of employee ids:

| Question template (10 products each) | Exact match |
|---|---|
| "&lt;role&gt; who worked on the previous release" (roles that author documents) | **40/40** |
| same, for QA specialists (they don't author documents and we don't model their involvement) | 0/10 |
| "engineer whose bug fixes were most often reopened" | 6/10 |
| "engineer assigned the most active bugs" | 1/10 |

Total: 47/70. The misses come from the 70 resolution messages we couldn't link to an issue (an unlinked resolution leaves the issue looking open) and from reassignment messages we don't parse yet. That is where the parser should improve next, and the evaluation shows it.

## Run it

**Docker (one container, FalkorDB + app):**
```bash
docker build -t lore .
docker run -p 7860:7860 lore            # open http://localhost:7860
# optional LLM chat:  docker run -p 7860:7860 -e ANTHROPIC_API_KEY=... lore
```

**Local development:**
```bash
docker run -d -p 6379:6379 -p 3000:3000 \
  -e FALKORDB_ARGS="TIMEOUT_DEFAULT 15000 TIMEOUT_MAX 60000 RESULTSET_SIZE 50000" falkordb/falkordb:v4.22.1
python -m venv .venv && .venv/bin/pip install -r requirements.txt
sh scripts/fetch_herb.sh data-raw/herb          # downloads the dataset from Hugging Face
python -m lore.load data-raw/herb               # builds the graph (~5 s)
uvicorn lore.api:app --port 8000                # UI at http://localhost:8000, MCP at /mcp
```
FalkorDB Browser at http://localhost:3000 shows the same graph (`company`).

**Use it from Claude Code or any MCP client:**
```bash
claude mcp add --transport http lore http://localhost:8000/mcp
# or stdio:  python -m lore.mcp_server
```

**Deploy:** `python scripts/deploy_hf.py` pushes the Docker setup to a Hugging Face Space.

## FalkorDB notes from building this
- The `falkordb/falkordb` image defaults to `TIMEOUT 1000` (1 second for reads) and `RESULTSET_SIZE 10000`. Override them with `FALKORDB_ARGS`.
- `[:R*1..$depth]`: a parameter isn't accepted as a variable-length bound ("Encountered unhandled type in inlined properties").
- `shortestPath()` is directed-only and only allowed in `WITH`/`RETURN`. Use `algo.SPpaths` with `relDirection: 'both'`.
- Full-text queries use RediSearch syntax, so user text is stripped of operators before querying.

## Limitations
- Bug-report parsing relies on HERB's message phrasing. Component names come from the message text, so some are generic ("API").
- Decisions exist only for TrendForce, KnowledgeForce and ContextForce. `trace_decision` says so when asked about other products.
- `COLLABORATED_WITH` is built from shared meetings only.

## AI assistance disclosure
Built during the event with **Claude Code (Claude Opus 5.5)** as a coding assistant. It was used for research, code generation and the offline extraction of decisions from meeting transcripts (`data-derived/decisions/`). Every extracted item is checked against its source transcript at load time. The graph model, tool design and queries were reviewed and tested against the live graph. Commits made with the assistant are marked `Co-Authored-By: Claude`.

## Data and license
Data: [HERB](https://huggingface.co/datasets/Salesforce/HERB) by Salesforce Research, a **synthetic** enterprise benchmark, licensed **CC-BY-NC-4.0**. No real people or private data are involved. The dataset is downloaded at build time and not redistributed here. Derived decision files are shared under the same license. Code: MIT.
