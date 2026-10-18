# What happens when someone leaves? Building a company brain that shows its work

*Lore, our Graph Hacks 2026 entry (Track 03, Company Brain), answers "what breaks if she leaves?" on FalkorDB, with the graph path and source behind every line.*

When an engineer hands in their notice, the useful questions are not about her profile. They are about what is attached to her. Which issues are still assigned to her, and which customers are waiting on them? Is she the only person who has ever fixed some component? Who could pick up each piece? Which of her meeting promises still stand?

None of those answers live in a single record. They live in how records connect: an issue to a component, a component to the same kind of component in another product, a commitment to the later meeting that changed it. We built Lore, a company brain on FalkorDB, to answer them under one rule: every answer shows its work. Lore returns the graph path behind each row, the source messages it rests on, and the exact Cypher that ran.

## The data: a synthetic company

We built on [HERB](https://huggingface.co/datasets/Salesforce/HERB), an enterprise benchmark from Salesforce Research, licensed CC-BY-NC-4.0. It is **synthetic**: no real people or private data. It models one company with 30 products, 530 employees, an org chart, 120 customers, Slack channels, documents, meeting transcripts and pull requests.

The loader rebuilds the graph at startup in about 5 seconds: roughly 40,000 nodes and 108,000 relationships, including 745 issues (309 open, 59 reopened, 377 resolved) across 198 components, 33,632 Slack messages and 89 decisions and commitments extracted from meetings. One Docker image runs FalkorDB `v4.22.1` (pinned) and the app.

## The graph model, briefly

The core labels are `Person`, `Product`, `Component`, `Issue`, `Customer`, `Document`, `Meeting`, `Decision` and `Message`, connected by edges such as `ASSIGNED_TO`, `IN_COMPONENT`, `AFFECTS`, `OWNED_BY` and `DECIDED_IN`. Issue lifecycle events (`REPORTED_IN`, `RESOLVED_IN`, `REOPENED_IN`) point at the Slack message that recorded them.

Two derived edges do most of the interesting work. `RESOLVED` links a person to an issue when they posted its resolution. `SAME_KIND_AS` links same-named components across products (`WHERE a.name = b.name AND a.id < b.id AND a.name <> 'Unspecified'`), so every product's "API Authentication Layer" is connected to the others.

The agent never writes Cypher. Lore has a fixed catalogue of tools, each with a hand-written, parameterised query, exposed over MCP and a REST API. The model picks a tool and fills in arguments, and every entity argument is resolved against the graph first. Memory is more nodes: `remember` writes a `Finding` linked `ABOUT` an entity, and `recall` returns findings with their dates.

## The hero question at depth 1 and depth 3

`if_person_leaves(person, depth)` is the tool we built the demo around. At depth 1 it asks for open work: issues currently assigned to the person that are not resolved.

```cypher
MATCH p = (a:Person {id: $pid})<-[asg:ASSIGNED_TO]-(i:Issue)-[:IN_COMPONENT]->(c:Component)
WHERE i.status <> 'resolved' AND asg.until IS NULL
RETURN i.id AS id, i.title AS title, i.status AS status, c.id AS component,
       [n IN nodes(p) | ...] AS path, [r IN relationships(p) | type(r)] AS rels  // node projection trimmed
```

For the engineer `eid_92c62291` (one of four Alice Browns, more on that below), depth 1 returns **7 open issues and 4 meeting commitments**. Each row carries its graph path, and each commitment carries its verbatim meeting quote. A join could have produced most of that.

Depth 2 adds the customers waiting on her issues, one entry per distinct customer, and any component where she is the only person who has ever resolved anything. Depth 3 (which includes depth 2) adds possible successors. Compared with depth 1, the answer gains **6 waiting customers** and, at depth 3, **7 possible successors**. She has no sole-expert component. The check exists, but it skips the generic `Unspecified` component, because a bucket for issues the parser could not place says nothing about expertise. The successor query is the one a simple join would not give us:

```cypher
MATCH (a:Person {id: $pid})<-[asg:ASSIGNED_TO]-(i:Issue)-[:IN_COMPONENT]->(c:Component)
WHERE i.status <> 'resolved' AND asg.until IS NULL
MATCH p = (a)<-[:ASSIGNED_TO]-(i)-[:IN_COMPONENT]->(c)-[:SAME_KIND_AS*0..1]-(c2:Component)<-[:IN_COMPONENT]-(:Issue)<-[:RESOLVED]-(s:Person)
WHERE s.id <> $pid
RETURN i.title AS issue, s.id AS id, s.name AS name, count(*) AS fixes, ...
ORDER BY fixes DESC
```

`SAME_KIND_AS*0..1` is the important part. Zero hops means "someone who fixed this component". One hop means "someone on another product who fixed the same kind of component", which finds a successor even when nobody on her team has touched it. Each candidate comes with the path to the fix it rests on.

## Which Alice? Refusing to guess

Ask "who is affected if Alice Brown leaves?" and Lore does not answer. It returns `ambiguous_entity`, because four employees are named Alice Brown: a VP of Engineering, two software engineers and a QA specialist. It lists each one with role and products, and asks you to pick by id.

A name that matches more than one person stops the tool before any traversal runs. An unknown name stops it too: ask `who_knows` about the "Quantum Flux Capacitor" and you get `unknown_entity`, and the trace shows only the name lookup. A name that exists with nothing connected gets a third outcome, `not_in_graph`, worded as "the knowledge isn't recorded, not that nobody has it". The optional Claude front end adds one check: if its reply names an `eid_` or `CUST-` id that no tool returned, the reply is replaced by the tools' own summaries.

The same idea applies at load time. Transcripts name speakers by display name, so the decision loader resolves a name only among that meeting's participants. When two participants share a name, it links both and marks the edges `ambiguous: true` (60 links in the dataset). All of this Alice's commitment edges resolved cleanly.

## Keeping knowledge current without overwriting it

A company brain goes stale if "current" is a field someone must remember to update. In Lore nothing is overwritten, and "current" is derived from history.

**Issue status** comes from the latest lifecycle event:

```cypher
MATCH (i:Issue)-[e:REPORTED_IN|RESOLVED_IN|REOPENED_IN]->(m:Message)
WITH i, e, m ORDER BY m.ts DESC
WITH i, collect(type(e))[0] AS last
SET i.status = CASE last WHEN 'RESOLVED_IN' THEN 'resolved' WHEN 'REOPENED_IN' THEN 'reopened' ELSE 'open' END
```

"Slow Response Time During Peak Usage" on InsightForce was resolved on August 26 and reopened on August 31, so it is `reopened`, and `issue_history` shows both messages in order.

**Documents** are chained per type with `SUPERSEDES`. The current version is the one nothing supersedes.

**Decisions** are current unless a later one `REVISES` them. This is why Alice shows 4 commitments, not 7. Three of her seven, including the "Optimize Database Schema for Real-Time Processing" PR, were handed to Bob Martinez in later ContextForce meetings. The commitments query filters on `OPTIONAL MATCH (later:Decision)-[:REVISES]->(t) ... WHERE later IS NULL`, so those three drop out and nothing is deleted.

**Assignments** carry an `until` property. An open assignment is one without it, which is why both queries above check `asg.until IS NULL`.

## Provenance: every edge says how it was made

Most of the graph comes from deterministic parsing in `lore/herb.py`: `eid_` mentions, `CUST-` ids, document and PR links, and the fixed phrasing HERB uses in bug-triage channels. No LLM is involved in that part.

Resolution messages were the hard case. 191 quote the issue title. Another 229 paraphrase it, so we match them against the open issues assigned to the sender in that channel: if there is exactly one, that is the match (84 messages); if there are several, we take the one with the most word overlap and require at least two shared words (145). Every lifecycle edge records its rule in a `match` property (`quoted-title`, `assignee-only-open-issue` or `assignee+overlap`), and `issue_history` returns it with each event. **70 resolutions and 13 reopens could not be matched, and we left them out** rather than guess. The result is 420 `RESOLVED_IN` edges.

Decisions and commitments are the only LLM-extracted content in the loaded graph. Claude extracted them offline from the transcripts of three products, each with a verbatim quote. At load time the check is a plain substring test, `d["quote"] not in m["transcript"]`, and failing items are rejected. Of 89 items, 0 were rejected, and the quote is what the UI shows as the source.

## Getting work done, with a human in the loop

`plan_handoff` drafts a reassignment for each open issue, picking the colleague with the strongest graph evidence (same product team first, then most fixes in this or a same-kind component). It writes a `proposed` `Action` node and nothing else. Only `apply_action(action_id, approved_by)` changes assignments, and the agent's instructions forbid calling it without explicit approval of a specific action id. Old assignments are closed, not deleted:

```cypher
UNWIND $steps AS s
MATCH (i:Issue {id: s.issue})-[old:ASSIGNED_TO]->(prev:Person)
WHERE old.until IS NULL AND prev.id <> s.to
SET old.until = $ts, old.ended_by = $id
```

A second query creates the new `ASSIGNED_TO` edges with `via` and `approved_by`, and links the `Action` to each issue it touched. For Alice, the plan proposes owners for all 7 open issues (Charlie Taylor, Charlie Davis and Julia Taylor among them). Re-run "if Alice leaves" after approval and **her open issues go from 7 to 0**. Her 4 meeting commitments remain, on purpose: the tool only hands off issues, and a promise made in a meeting has to be reassigned by a person.

## What FalkorDB taught us

- **A variable-length bound can't be a parameter.** `[:REPORTS_TO*1..$d]` fails with "Encountered unhandled type in inlined properties." We clamp depth to 1..3 in Python and let it choose which fixed queries run, so it never enters the query text.
- **`shortestPath()` is directed-only.** An undirected pattern returns "FalkorDB does not currently support undirected shortestPath traversals". `connect(a, b)` uses the algorithm procedure instead:

  ```cypher
  MATCH (x {id: $a}), (y {id: $b})
  CALL algo.SPpaths({sourceNode: x, targetNode: y, relTypes: [...], relDirection: 'both', pathCount: 1, maxLen: 8})
  YIELD path AS p
  ```

- **Check the image defaults.** `falkordb/falkordb` ships with `FALKORDB_ARGS=MAX_QUEUED_QUERIES 25 TIMEOUT 1000 RESULTSET_SIZE 10000`: one second for read queries and 10,000 rows per result. We set our own limits in the Dockerfile.
- **Full-text search uses RediSearch syntax.** Punctuation in user text can be read as operators, so every string goes through an `ft()` helper that replaces `[^\w\s]` with spaces before it reaches `db.idx.fulltext.queryNodes`.

The built-in algorithms saved time too: `key_people` is `algo.pageRank` plus `algo.betweenness` over a derived `COLLABORATED_WITH` graph.

## Limitations

The parser depends on HERB's phrasing. Component names come from a regex, so some are generic ("API"), and issues without a recognisable component land in an `Unspecified` bucket. Early in the build, an apostrophe-versus-quote regex bug produced 36 junk issue titles (starting with "s ", from "customer's"). It was fixed by requiring that quote marks do not touch letters. 

## Checking it against ground truth

HERB ships its own "person" questions, and some have answers that are pure graph patterns. We scored Lore against those with `eval/herb_eval.py`, 47 of 70 in total:

- "Who worked on the previous release", for document-authoring roles: 40 of 40. QA specialists score 0 of 10, because they do not author documents and we do not model their involvement.
- "Which engineer's fixes were most often reopened": 6 of 10.
- "Which engineer has the most active bugs": 1 of 10.

The misses trace to two gaps. The 70 resolution messages we could not link leave some issues looking open, which inflates the active-bug counts. And we do not parse reassignment messages, so some issues sit with the wrong engineer.

Decisions exist only for TrendForce, KnowledgeForce and ContextForce, and `trace_decision` says so when asked about anything else. Collaboration is inferred from shared meetings only. Successors are ranked by past fixes, not current workload.

## What's next

First, the linking gaps behind the evaluation misses: more of the 70 unmatched resolutions, and reassignment messages. Then incremental ingest: since every "current" is derived from append-only history, new messages only need to add events and re-run the derivations. After that, quote-checked decision extraction for all 30 products.

We used Claude Code (Claude Opus 5.5) as a coding assistant during the event, and Claude did the offline decision extraction, which is why every extracted item has to pass the quote check.

The code is at [github.com/Petsteb/lore-company-brain](https://github.com/Petsteb/lore-company-brain). Any MCP client can act as the agent: `claude mcp add --transport http lore http://localhost:8000/mcp`.
