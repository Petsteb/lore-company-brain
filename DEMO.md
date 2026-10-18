# Demo script (about 3 minutes) and how to explain Lore

## Before recording
- `docker build -t lore . && docker run -p 7860:7860 lore` (rebuild first, so the image has the current UI), or use the live Space. Wait for "Uvicorn running". The graph rebuilds at every start, so each recording starts from clean data.
- Approving a handoff changes Alice's assignments, which is the point, but it alters the other Alice views. Click **Undo handoff**, or **Reset demo data** on the home page, between takes.
- To reset without restarting: `python -m lore.load data-raw/herb` (about 5 seconds).
- Optional second window: FalkorDB Browser on `:3000` (local dev setup) to show the raw graph.

## Script

A captioned recording of this walkthrough can be regenerated with `python scripts/record_demo.py` (writes `demo/lore-demo.mp4`). A narrated version in your own voice is better: follow the same steps.

**0:00 What it is (15 s).** Home page. "Lore is a company brain on FalkorDB: about 40 thousand records and 108 thousand relationships from a synthetic company. You look something up, then ask a question about it, and every answer shows the records it came from."

**0:15 Start from a name (20 s).** Type **Alice Brown** in the search.
- Four people share the name. Each row shows role and products. "It won't guess. A wrong person here is worse than no answer."
- Pick the **Software Engineer on TrendForce** (`eid_92c62291`). Choose **What if they leave?**

**0:35 Reach is the point (40 s).** The control is **How far to look: Direct · Team · Company-wide**.
- **Direct:** 7 open issues and 4 meeting commitments, each with a source.
- **Team:** adds the 6 customers waiting on those issues. Lore also checks for any part of the product she's the only one to have fixed (none for her).
- **Company-wide:** adds 7 people who could step in.
- Point at a successor thread in the graph: "This person never worked on this product. They fixed the same kind of component somewhere else. That's three hops, and a join or a vector search doesn't find it."

**1:15 Get work done (35 s).** Choose **Hand off their work**, then **Draft handoff**.
- Every open issue gets a suggested owner with the reason. Nothing has changed yet.
- Click **Approve handoff**, then **See what's still at risk**. Open issues go from 7 to 0. The 4 meeting commitments remain, because people reassign those.

**1:50 Knowledge changes (25 s).** Search **Inadequate Handling of Missing Data**, then open **History**.
- It was resolved on 07-28, then the customer reopened it the same day, so it reads as reopened.
- "Nothing is overwritten. Status comes from the latest event, and each step quotes its Slack message."

**2:15 Who knows what (20 s).** Open the **API Authentication Layer** component, then **Who knows this?** Switch from Direct to Company-wide: design-doc authors and people from other products join the list.

**2:35 Decisions (15 s).** Open **TrendForce**, then **Decisions**. Each decision has its meeting and a verbatim quote; ones changed later are marked revised.

**2:50 Honesty and transparency (10 s).** Search for something that isn't there: nothing matches. Then open **How this was answered** on any answer: "Hand-written Cypher. The model only chooses which question to ask, through MCP or the API."

## How to explain it (likely judge questions)

**Why a graph?** The questions are about connections: person → issue → component → other components → other people. Take FalkorDB out and none of the tools work. They are traversals plus PageRank, betweenness and SPpaths.

**Where does the data come from?** HERB by Salesforce Research, a synthetic enterprise benchmark (CC-BY-NC-4.0), so there's no personal data. It is downloaded at build time (`scripts/fetch_herb.sh`).

**How is the graph built?** `lore/herb.py` parses deterministically:
- employee IDs, `@eid_` mentions, `CUST-` ids, document and PR links;
- the fixed phrasing of the bug-triage threads ("issue titled '…'", "has been resolved", "reopened").

`lore/load.py` writes everything with `UNWIND $rows` batches, then computes the derived edges: `status`, `RESOLVED`, `COLLABORATED_WITH` and `SAME_KIND_AS`.

**What about paraphrased bug resolutions?** They are matched to the sender's own open issues by word overlap. The `match` property on every lifecycle edge records how it was matched. The 70 we couldn't match are left out.

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
