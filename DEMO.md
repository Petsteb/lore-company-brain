# Demo script (about 3 minutes) and how to explain Lore

## Before recording
- `docker run -p 7860:7860 lore` (or the live Space). Wait for "Uvicorn running". The graph rebuilds at every start, so each recording starts from clean data.
- Run **"Draft a handoff → Approve"** last. Approving changes Alice's assignments, which is the point, but it alters the earlier presets.
- To reset without restarting: `python -m lore.load data-raw/herb` (about 5 seconds).
- Optional second window: FalkorDB Browser on `:3000` (local dev setup) to show the raw graph.

## Script

**0:00 What it is (15 s).** "Lore is a company brain on FalkorDB. It holds 42 thousand nodes and 108 thousand relationships from a synthetic company: 530 people, 30 products, Slack, docs, meetings, pull requests and bug reports. Every answer is a path through that graph, and you can see the queries that produced it."

**0:15 The question that matters (40 s).** Click **"Who is affected if Alice Brown leaves?"**
- The result is *ambiguous_entity*: there are four Alice Browns. "It won't guess. A wrong person here is worse than no answer."
- Click the Software Engineer, `eid_92c62291`. Then click **depth 1**: 8 open issues and 4 meeting commitments, each with a source.

**0:55 Depth is the point (35 s).** Click **depth 3**.
- New results appear: 8 customers waiting on her, a component where she is the *only* person who has ever fixed anything, and 7 possible successors.
- Point at a successor path in the graph: "This person never worked on this product. They fixed the same kind of component somewhere else. That's three hops, and a SQL join or a vector search doesn't find it."

**1:30 Knowledge changes (30 s).** Click **"History of 'Inadequate Handling of Missing Data'"**.
- The issue was resolved on 07-28 by George Jones, then reopened by the customer the same day, so the current status is **REOPENED**.
- "Nothing is overwritten. Status is derived from the latest event, and each event links to the Slack message it came from. This resolution was matched by assignee plus word overlap, and the edge records that."

**2:00 Decisions with sources (25 s).** Click **"Trace the Kubernetes decisions"**.
- 7 decisions match and 5 are current. Some were revised in later meetings.
- Show a source quote. "Each decision was extracted by an LLM offline, but it carries a verbatim quote. The loader rejects anything that isn't found word for word in the transcript."

**2:25 Get work done, with a human in the loop (25 s).** Click **"Draft a handoff of Alice's work"**.
- The result is *action_proposed*: every issue has a suggested owner and the evidence for it. Nothing has changed yet.
- Click **Approve & apply**. Then re-run **depth 1**: 8 open issues are now 1. The remaining one has nobody with relevant experience, so it is left for a human.

**2:50 Honesty (10 s).** Click **"Ask about something not in the graph"**. The result is *unknown_entity* and no query ran.
- Open the **Queries run** panel. "Every query is hand-written Cypher. The model only picks tools, through MCP or the API, and never writes queries."

## How to explain it (likely judge questions)

**Why a graph?** The questions are about connections: person → issue → component → other components → other people. Take FalkorDB out and none of the tools work. They are traversals plus PageRank, betweenness and SPpaths.

**Where does the data come from?** HERB by Salesforce Research, a synthetic enterprise benchmark (CC-BY-NC-4.0), so there's no personal data. It is downloaded at build time (`scripts/fetch_herb.sh`).

**How is the graph built?** `lore/herb.py` parses deterministically:
- employee IDs, `@eid_` mentions, `CUST-` ids, document and PR links;
- the fixed phrasing of the bug-triage threads ("issue titled '…'", "has been resolved", "reopened").

`lore/load.py` writes everything with `UNWIND $rows` batches, then computes the derived edges: `status`, `RESOLVED`, `COLLABORATED_WITH` and `SAME_KIND_AS`.

**What about paraphrased bug resolutions?** They are matched to the sender's own open issues by word overlap. The `match` property on every lifecycle edge records how it was matched. The 67 we couldn't match are left out.

**Where is the LLM used?**
1. Offline, to extract decisions and commitments from 29 meeting transcripts. Each item needs a verbatim quote, which `lore/decisions.py` checks.
2. Optionally at runtime, to pick tools (`/api/ask` with an API key, or any MCP client). A reply that names an id no tool returned is rejected (`lore/agent.py`).

**Why not let the LLM write Cypher?** Generated queries can be subtly wrong, expensive, and impossible to audit afterwards. Fixed parameterised tools let us validate arguments and resolve names against the graph first. No model text is ever concatenated into a query.

**How is ambiguity handled?** Names resolve to IDs before any reasoning query runs. If two or more people match, the result is `ambiguous_entity` with the candidates. Meeting speakers are matched only among that meeting's attendees, and shared names are linked to every candidate and marked `ambiguous: true`.

**How does knowledge stay current?**
- The latest lifecycle event sets issue status.
- `SUPERSEDES` chains mark the current document version.
- `REVISES` marks the current decision.
- A handoff sets `until` on old assignments instead of deleting them.

**What does "depth" mean?** The tool runs more traversals at higher depth. FalkorDB doesn't accept a parameter as a `*1..$d` bound, so depth selects between fixed queries instead of being put into query text.

**Memory?** `remember` and `recall` write and read `Finding` nodes `ABOUT` any entity, with dates and session ids. Proposed and applied `Action` nodes are also memory: who approved what, and when.

**Graph algorithms?**
- `algo.pageRank` and `algo.betweenness` over `COLLABORATED_WITH` (from shared meetings) find load-bearing people.
- `algo.SPpaths` with `relDirection:'both'` powers `connect`, because `shortestPath()` is directed-only in FalkorDB.

**Limitations.**
- Component names come from free text, so some are generic ("API").
- Decisions exist for 3 of 30 products.
- Collaboration is built from meetings only.

**AI use.** Built with Claude Code as a coding assistant. That is disclosed in the README, and the commits carry Co-Authored-By trailers.
