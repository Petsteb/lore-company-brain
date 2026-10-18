# Ecosystem Digest: Track 03 "Company Brain" (Graph Hacks 2026)

Researched 2026-10-17. Raw downloads are in
`C:\Users\petst\AppData\Local\Temp\claude\C--Info-Hackathons-Graph-Hacks-2026\e98f7fa8-13eb-43ff-982d-44b9276a5e21\scratchpad\ecosystem\`.
Version numbers come from the PyPI JSON API, the GitHub API and the Docker Hub API, checked on that date.
Anything I could not confirm from a primary source is marked **UNVERIFIED**.

---

## 0. TL;DR recommendations

| Decision | Recommendation | Why (short) |
|---|---|---|
| Framework vs raw client | **Raw `falkordb` Python client (v1.8.0) as the spine.** Write our own schema and a fixed catalogue of parameterized Cypher tools exposed over MCP. Use an LLM only to extract decisions and claims from free text (transcripts, docs, chat), and write those results with deterministic IDs. Graphiti is an optional add-on for a narrow "chat facts" layer. GraphRAG-SDK is optional for document-chunk retrieval. | The organizer blog explicitly asks for human-written Cypher tools, entity resolution against the graph, and `nodes(path)` in every answer. Frameworks own their schema. GraphRAG-SDK puts every relation on one `RELATES` edge and keys entities by **name**. Our best dataset has 530 people but only 98 unique names (see section 6), so name-keyed identity would merge different people. *That merge is inferred from the documented ID derivation and was not tested. The raw-client recommendation stands without it, because it rests on the organizer guidance and deterministic supersession.* Deterministic `SUPERSEDES` / `valid_from` / `valid_to` edges give a reproducible "knowledge stays current" demo, whereas framework invalidation depends on the LLM. |
| Primary demo data | **Synthetic: Salesforce AI Research "HERB" dataset (HF `Salesforce/HERB`), one to three products only.** It has an org chart, employees, products and teams, plus Slack, meeting transcripts, docs and PRs, all with IDs and timestamps. It also ships answerable questions with citation IDs and unanswerable questions. | It is synthetic, so the personal-data rule is not an issue. It is already structured for "who owns / who knows". The question sets give us a built-in eval for both "answers with sources" and "not in the graph". License is CC-BY-NC-4.0, which fits a non-commercial hackathon if we credit the source. |
| Backup / "real org" data | **GOV.UK public engineering data** (`alphagov/govuk-rfcs` + `alphagov/govuk-developer-docs/data/repos.yml` + per-repo `docs/adr`). The alternative is **Kubernetes KEPs** (`kep.yaml` with owning-sig / approvers / status / replaces). | Real decisions with real supersession chains. GOV.UK ownership is recorded as **team → repo** (team = Slack channel name), with no personal data. KEPs carry GitHub handles only. |
| Avoid | Enron emails, public mailing-list archives, `kubernetes/community/sigs.yaml` email fields, PEP author emails, the Cognee FalkorDB adapter | Personal-data rule; the Cognee adapter is fragile (see section 3). |
| FalkorDB server | **Pin `falkordb/falkordb:v4.22.1`** and do not rely on `latest` | The v6.0.0 (Rust engine) release notes say `latest` moves to 6.0. The Docker Hub API on 2026-10-17 shows `latest` with the same digest as `v4.22.1` (`sha256:b0bdcac2cbaff…`). Frameworks have not been checked against 6.0 (**UNVERIFIED**). Versions before 4.20.5 have published CVEs (see section 9). |

---

## 1. FalkorDB GraphRAG-SDK

- **Repo:** https://github.com/FalkorDB/GraphRAG-SDK (Apache-2.0, ~1.0k stars, last push 2026-10-14). **Docs:** https://docs.falkordb.com/graphrag
- **Current version:** `graphrag-sdk` **1.4.0** (PyPI, uploaded 2026-08-10), Python >= 3.10. v1.0 (April 2026) was a rewrite, and `graphrag-sdk==0.8.2` is the legacy pin for the old v0.x API. Ignore any blog or tutorial written before April 2026: the API changed completely.
- **Core dependencies:** `falkordb>=1.0,<2`, **`gliner`** (local NER model, which pulls **PyTorch** and transformers, so expect a heavy install on Windows), `hnswlib`, `scipy`, `tiktoken`.
  Extras: `litellm`, `openai`, `anthropic`, `cohere`, `openrouter`, `pdf`, `pdf-fast`, `markdown`, `spacy`, `fastcoref`, `sentence-transformers`.
- **LLM providers:** `LiteLLM` / `LiteLLMEmbedder` is the recommended default and covers 100+ providers: OpenAI, Azure, Anthropic, Cohere and others. `OpenRouterLLM` / `OpenRouterEmbedder` are also available, or you can subclass `LLMInterface` / `Embedder`. An embedder is mandatory.

### How extraction and the ontology work
- **Ingestion is a 9-step pipeline:** Load → Chunk → Lexical graph (`Document`-`PART_OF`-`Chunk`, `NEXT_CHUNK`) → Extract → Quality filter / Prune against schema → Resolve (dedup) → Write (batched `UNWIND MERGE`, 500 per batch) → in parallel, `MENTIONED_IN` edges and chunk embeddings.
- **Extraction is a 2-step hybrid.** **GLiNER** (local) proposes entities. An LLM then verifies them and extracts relationships with evidence. Coreference resolution is optional.
- **Ontology sources:**
  - Built-in defaults: 11 entity types (Person, Organization, Technology, Product, Location, Date, Event, Concept, Law, Dataset, Method) and about 31 preferred relation labels.
  - A hand-written `Ontology(...)` / `GraphSchema(...)`.
  - `Ontology.from_sources(sources, llm, method="llm"|"grounded")`, which **auto-discovers** an ontology from samples of the corpus (summary per doc, proposals per chunk, then a normalization pass).
  - `suggest_schema_extensions` as new docs arrive, plus an evolution API to rename types and add attributes.
- **Graph schema written:** `Document`, `Chunk` (with embedding) and `__Entity__` (secondary label plus a type label). **All extracted relations use one edge type, `RELATES`**, with the original type stored in a `rel_type` property and a `fact` string. Hand-written tool Cypher therefore has to filter `WHERE r.rel_type = 'OWNS'` instead of matching `-[:OWNS]->`.
- **Entity IDs come from the name**, for example `maya_ellison__person`. Post-ingest dedup groups entities by (normalized name, label). This breaks on datasets with many repeated names (see the HERB check in section 6).

### Provenance
- Yes, at chunk level. `Entity -[:MENTIONED_IN]-> Chunk -[:PART_OF]-> Document`. Entities also carry `source_chunk_ids` and character `spans`.
- `rag.completion(q, return_context=True)` returns the retrieval trail. There is an official "grounded answers with abstention" example (`graphrag_sdk/examples/grounded_answers_with_abstention.py`), which fits the "honestly says not in the graph" requirement.

### Other features relevant to us
- **Structured ingestion (2026-Q2).** You declare a `TableMapping` (key column, name column, typed properties, `Link("WORKS_AT", to="Organization", by="org_id")`) inside the ontology, and a CSV is then written **deterministically, with no LLM involved**. Rows become `Chunk{kind:"record"}` and properties are "signed" with their source file (`employees__age`). Prose and table facts merge onto the same entity node.
- **Incremental updates (v1.1).** `update()` / `delete_document()` / `apply_changes(added, modified, deleted)` use SHA-256 no-op detection, crash-safe cutover and orphan cleanup. Updates are **document-level only**. There is no fact-level validity: the temporal graph is on the "2026-Q4" roadmap and `docs/graphrag/temporal` returns 404.
- **Retrieval.** Multi-path: keyword extraction, then vector search over `RELATES` facts, `CONTAINS` + full-text entity search, 1-2 hop expansion, and 4-path chunk retrieval, followed by a cosine rerank. Text-to-Cypher is described as "experimental".

### Ingestion speed
- FalkorDB's own benchmark (gpt-4o-mini, 24-core machine): **510 chunks took about 1.5 h; about 2,500 chunks took about 4 h** end to end, mostly waiting on extraction API calls. Source: https://docs.falkordb.com/graphrag/benchmark
- That works out to roughly 10 s per chunk at default concurrency. For our time budget, plan on **at most a few hundred chunks** through LLM extraction, and send everything already structured through deterministic loading.

### Strengths and weaknesses for Company Brain
- **Strengths:**
  - It is the sponsor's own SDK, which likely earns goodwill with judges (**UNVERIFIED**, but WeMakeDevs is explicitly a sponsor go-to-market vehicle).
  - Chunk-level provenance and an abstention example come built in.
  - Deterministic CSV ingestion.
  - Incremental document updates.
  - Fast to stand up: about 30 lines to ingest and answer.
- **Weaknesses:**
  - Single `RELATES` edge type.
  - Name-keyed entity identity.
  - No fact-level temporal model.
  - torch/GLiNER install weight.
  - Answer generation is a black box unless we write our own tools. Organizers want a fixed tool catalogue rather than LLM-generated Cypher or retrieval soup.
- **Best use for us:** an optional "document chunk search" tool or helper inside our own catalogue, pointed at a **separate graph name**, or used only for its loaders and chunkers. It should not be the main schema. Note that FalkorDB cannot traverse across graphs, so if we want `(:Decision)-[:CITES]->(:Chunk)` traversals, everything must sit in one graph.

---

## 2. Graphiti (getzep/graphiti) with FalkorDB

- **Repo:** https://github.com/getzep/graphiti (Apache-2.0, ~31.8k stars, very active). **Version:** `graphiti-core` **0.31.1** (PyPI, 2026-10-15), Python >= 3.10.
- **Install:**
  - `pip install "graphiti-core[falkordb]"`, which pins `falkordb>=1.1.2,<2`.
  - Optional LLM extras: `[falkordb,anthropic,google-genai]` (also `groq`, `voyageai`, `sentence-transformers`, `gliner2`).
  - `[falkordblite]` gives an embedded FalkorDB with no Docker (Python 3.12+).
- **Driver code:**
  ```python
  from graphiti_core import Graphiti
  from graphiti_core.driver.falkordb_driver import FalkorDriver
  from graphiti_core.nodes import EpisodeType
  driver = FalkorDriver(host="localhost", port=6379, database="company_brain")  # graph name; default "default_db"
  g = Graphiti(graph_driver=driver)
  await g.add_episode(name="slack#planning 2026-03-12", episode_body=text,
                      source=EpisodeType.message, source_description="Slack #planning",
                      reference_time=msg_ts)          # also EpisodeType.text / .json
  edges = await g.search("Who owns the auth service?")  # each edge: .fact, .valid_at, .invalid_at, .episodes
  ```
  Source: `examples/quickstart/quickstart_falkordb.py` in the repo.

### Temporal model and provenance
`EntityEdge` fields, verified in `graphiti_core/edges.py` on main:
- `fact`
- `episodes: list[str]`: IDs of the episodes that produced or support the edge. This is our provenance link back to source messages and docs.
- `valid_at`: when the fact became true in the world.
- `invalid_at`: when it stopped being true.
- `expired_at`: when the system invalidated it (the transaction-time half of the bi-temporal model).
- `reference_time`
- `created_at`

On each new episode, `resolve_extracted_edges` returns `(resolved, invalidated, new)`. Contradicted edges are **invalidated, not deleted**, so you can query "what was true at time T". Since the current main, **`add_episode_bulk` also performs invalidation and date extraction**. Older docs say bulk skips it, and they are out of date.

### LLM requirements
- Defaults to OpenAI for both the LLM and embeddings, and needs `OPENAI_API_KEY`.
- The README says it "works best with LLM services that support Structured Output (OpenAI, Anthropic, Gemini)". Anthropic, Gemini and Groq are supported, as are OpenAI-compatible endpoints and local models.
- You still need an embedder: OpenAI, Voyage, Gemini or sentence-transformers.
- Concurrency is set by `SEMAPHORE_LIMIT` (default 10). Raise it if your rate limits allow.

### Speed and cost
- **UNVERIFIED:** no official per-episode figure exists. A third-party mirror of the docs claims **6-10 LLM calls per episode** (extraction, node dedup, edge extraction and dedup, date and invalidation, summaries). That puts a few seconds to tens of seconds on each episode, depending on the model.
- For about 1,000 Slack messages, batch them into episodes (for example, one episode per thread or per day per channel) and use `add_episode_bulk`. Measure on a 20-episode sample first.

### MCP server
- `mcp_server/` in the repo **defaults to FalkorDB**. `docker compose -f docker/docker-compose.falkordb.yaml up` starts FalkorDB, the Browser on :3000 and an MCP endpoint at `/mcp/`.
- Tools: add_memory, search_nodes, search_facts, episodes and others.
- Open issues: #1991 and #1911 report that it drops queued episodes on a single 429 error or on transient extraction errors.

### Known FalkorDB-driver pitfalls (open issues, checked 2026-10-17)
- **#1842:** multi-`group_id` reads (`get_episodes` / `search_nodes`) silently query the wrong graph.
- **#1876:** `group_id` selects the database for writes but not for reads.
- **#1934:** `FalkorDriver.clone()` schedules un-awaited index builds, causing "Connection closed by server" errors with group scoping.
- **#1885:** nested attribute values abort ingestion on FalkorDB.
- **What to do:** use **one graph and no `group_id` scoping**, and keep attributes flat. Because FalkorDB graphs are isolated, any deterministic spine nodes we write ourselves must go into the **same graph name** that Graphiti uses (`database=`) if we want to traverse between them.

### Fit for Company Brain
- **Strong concept fit** for "keeping knowledge current" and "tracing decisions to source" (`episodes` on every fact).
- **Risks:**
  - Invalidation is decided by the LLM, so a live demo is not deterministic.
  - Graphiti owns the schema (`Entity`, `Episodic`, `RELATES_TO`, `MENTIONS`, `Community`, `Saga`), which clashes with "human-written Cypher over our own model".
  - Driver bugs listed above.
  - Cost and latency per episode.
- **Use it for:** an optional "chat / meeting facts" layer, with every `EntityEdge` linked to our deterministic `Message` / `Document` nodes through `episodes`. Do not use it as the main model.

---

## 3. Other frameworks (one paragraph each)

**Cognee** (https://github.com/topoteretes/cognee, Apache-2.0, ~32k stars, `cognee` 1.6.5 on 2026-10-16)
- Agent "memory platform": `add()` → `cognify()` (LLM graph extraction) → `search(SearchType.GRAPH_COMPLETION, …)`. It has DLT connectors (Slack export, Notion, Confluence, Gmail) with incremental sync and "forget-on-delete".
- FalkorDB support is a **community adapter**, `cognee-community-hybrid-adapter-falkor` 0.4.0. It **pins `cognee==1.4.2` and `litellm==1.89.2`**, two minor versions behind cognee and a fragile combination. The adapter also needs Python 3.11-3.13 and a SQLite relational store next to FalkorDB. Docs: https://docs.falkordb.com/agentic-memory/cognee
- **Verdict:** skip. The dependency risk is high, and FalkorDB would be one of several stores, which weakens the "FalkorDB does the real work" story. The idea worth borrowing is **forget-on-delete**: the Cognee hackathon winner "Lethe" won partly because it forgets removed systems (section 8).

**QueryWeaver** (https://github.com/FalkorDB/QueryWeaver, **AGPL-3.0**, `queryweaver` 0.3.2)
- FalkorDB's **Text2SQL** tool. It models a relational DB **schema** (tables, columns, FKs) as a graph in FalkorDB, then uses it to choose tables and joins and generate SQL. It offers REST, an MCP server (`list_databases`, `connect_database`, `database_schema`, `query_database`) and per-user memory graphs with a TTL. Docker: `docker run -p 5000:5000 falkordb/queryweaver`.
- **Verdict:** not useful directly, since we have no SQL source. The AGPL license also means **do not copy its code**. Two ideas are worth borrowing: "schema graph as context" and per-user memory graphs with a TTL.

**LangChain FalkorDB** (`langchain-falkordb` 0.2.0, https://github.com/FalkorDB/langchain-falkordb; JS: `@falkordb/langchain-ts`)
- Provides `FalkorDBGraph` (schema introspection plus `GraphDocument` ingestion), `FalkorDBVector` (vector, hybrid and MMR search), `FalkorDBQAChain` (NL → Cypher, i.e. LLM-generated Cypher, which the organizers advise against), `FalkorDBSaver` (LangGraph checkpointer) and `FalkorDBChatMessageHistory`.
- **Verdict:** only useful if we build the agent loop in LangGraph. `FalkorDBSaver` / chat history would then keep agent memory in FalkorDB too, which earns some "memory as nodes" credit. Not needed otherwise.

**LlamaIndex FalkorDB** (`llama-index-graph-stores-falkordb` 0.7.0)
- `FalkorDBGraphStore` for `KnowledgeGraphIndex`: LLM triplet extraction plus Cypher retrieval.
- **Verdict:** a generic KG-RAG with no temporal support and no provenance beyond nodes. It adds little over GraphRAG-SDK. Skip it.

**FalkorDB MCP Server** (https://github.com/FalkorDB/FalkorDB-MCPServer, npm `@falkordb/mcpserver`, Docker `falkordb/mcpserver`)
- Generic Node.js MCP server: run arbitrary OpenCypher, list and delete graphs, `FALKORDB_DEFAULT_READONLY=true`, stdio or HTTP transport.
- **Verdict:** handy for development (Claude Desktop pointed at our graph). It is the **opposite** of what organizers want in the product, which is a fixed tool catalogue. Write our own MCP server (Python `mcp` / FastMCP) whose tools wrap our Cypher.

**Graphify** (`pip install "graphifyy[falkordb]"`)
- Coding-assistant skill that turns a repo, docs or notes into a knowledge graph and exports it to FalkorDB.
- **Verdict:** could quickly graph a docs folder. It is low-control and LLM-built. Optional.

**Mem0** has a FalkorDB page as well (https://docs.falkordb.com/agentic-memory/mem0). It targets agent and user memory, so it fits Track 2 better than ours.

---

## 4. FalkorDB integrations page and community projects

**https://docs.falkordb.com/integration/:**
- REST API, Kafka Connect sink, Apache Jena (RDF/SPARQL), Bolt protocol (experimental; a CVE write-up says Bolt was removed in 4.20.0 per CVE-2026-107910, though the docs still list it, so this is **UNVERIFIED**), Spring Data FalkorDB, Snowflake Native App, **n8n GraphRAG nodes**, PyTorch Geometric, Graphify, and the **`falkordb-bulk-loader`** (1.2.0, CSV → binary bulk insert).
- The GenAI tools section (https://docs.falkordb.com/genai-tools/) adds GraphRAG-SDK (also hosted as "GraphRAG Server", https://graphrag.falkordb.com/), AG2, LangChain, LangGraph, LlamaIndex, AWS GraphRAG Toolkit, the MCP Server, QueryWeaver and Code-Graph.
- The Agentic-memory section adds Graphiti, the Graphiti MCP server, Cognee and Mem0.

**Native features worth using so the graph visibly "does work"** (from the docs tree):
- Vector index, full-text index, range index.
- Algorithms: BFS, shortest path (SP/SS), A\*, **PageRank**, **betweenness**, harmonic centrality, **WCC**, **CDLP (label-propagation communities)**, MSF, max-flow.
- FLEX UDFs: `flex.text.jaroWinkler`, `flex.text.levenshtein` and `flex.sim.jaccard`, all useful for **entity-name resolution in Cypher**, plus date parse/format and JSON helpers.
  - FLEX is a **separate JavaScript UDF package** (https://github.com/FalkorDB/flex). You load it with `db.udf_load(name, script)` / `GRAPH.UDF LOAD`.
  - Whether the `v4.22.1` image ships it preloaded, or supports `GRAPH.UDF` at all, is **UNVERIFIED**. Test it first.
  - Fallback: a full-text index plus Python-side fuzzy matching (e.g. `rapidfuzz`).
- Multi-graph per instance (one graph per tenant).

Use cases for us:
- **Who knows X:** PageRank or betweenness over the Person–Doc–Topic subgraph.
- **Bus factor / knowledge silos:** WCC or CDLP communities.
- **Who could help:** shortest path between two people.

**Community projects (https://www.falkordb.com/community-projects/).** These are mostly articles, not code:
- HackerNoon "One tool you need to scale RAG" (FalkorDB + KubeMQ)
- TrustGraph podcast with the FalkorDB CTO (linear-algebra graphs)
- "GraphRAG: Knowledge Graph & LLM" (Medium)
- Mental-health QA chatbot with FalkorDB + LlamaIndex
- Top OSS repos visualized with **Code Graph**
- Advanced RAG with FalkorDB + LangChain + Diffbot
- BFSI GraphRAG assistant
- AG2 trip-planning swarm with a FalkorDB GraphRAG agent

**Nothing company-knowledge-specific appears on that page, so there is no direct duplicate.** Related prior art to borrow ideas from:
- The organizer's own **"Blast Radius"** demo (service dependency graph, on-call, `nodes(path)`).
- GOV.UK's **`alphagov/govuk-knowledge-graph-gcp`** (a real government content knowledge graph). Seen in a GitHub code search result; contents not reviewed.

---

## 5. FalkorDB code-graph

- **Repo:** https://github.com/FalkorDB/code-graph (MIT, ~350 stars, last push 2026-10-12). Live demo: https://code-graph.falkordb.com/
- **Stack:** FastAPI backend plus React/Vite frontend. Analyzers for **Python, Java, C#** produce File/Class/Function nodes with call and dependency edges. It also has `git_utils/` (**a git history graph**, so you can switch the graph to a given commit), code-coverage utilities, and an LLM chat (`/api/chat` through LiteLLM; default model `gemini/gemini-flash-lite-latest`) built on GraphRAG-SDK.
- **Run:** Python >= 3.12 < 3.14, Node 20+, `uv`, `docker-compose.yml` (FalkorDB + app). `/api/analyze_repo` clones and graphs a public repo.
- **Relevance:** only if our Company Brain ingests GitHub repos. We could then link `(:Person)-[:AUTHORED]->(:Commit)-[:TOUCHES]->(:File)<-[:DEFINED_IN]-(:Function)` to decisions (ADR mentions file or service). It is MIT, so code can be borrowed with attribution. **Not recommended for the core.** It fits Track 1 better, and HERB PRs have no code.

---

## 6. Legally usable data sources

**Rule test:** "public, synthetic, or your own; NO private, login-protected, paywalled or personal data". Real names and emails of real people are the main risk, even when they are public. When using real OSS data, **drop names and emails and keep GitHub handles or team names only.**

| Source | What it gives the graph | Rule check | Demo impact | Notes |
|---|---|---|---|---|
| **HERB** (HF `Salesforce/HERB`, paper arXiv 2506.23139, code github.com/SalesforceAIResearch/HERB) | `metadata/employee.json` (530 people: `eid_`, name, role, location, org); `salesforce_team.json` (**hierarchical org chart**: VP → Eng Leads → Engineers/QA); `customers_data.json`; 30 product files. Each product file (about 1 MB) has the team (eid list), customers, about 1,000 Slack messages (channel, user eid, timestamp), about 15 docs (author eid, date, type), about 10 meeting transcripts (participant eids, date, type), PRs with reviews, URLs, **answerable_questions with `ground_truth` + `citations` (artifact IDs)**, and **unanswerable_questions**. | **Synthetic** (generated with GPT-4o). License **CC-BY-NC-4.0**: non-commercial use with attribution, which is OK for a hackathon. The card says "research purposes" and "do not use to build models that compete with OpenAI". | **Highest.** Covers people, teams, products, docs, chats, meetings, PRs and timestamps, and ships an evaluation set for "sources" and "not in the graph". | **Checked by hand:**<br>(1) **Only 98 unique names across 530 employees**: 92 names are shared, and up to 11 people are called "Hannah Garcia". **Key people by `eid_`, never by name.** Resolve speaker names in transcripts against that product's team list or the meeting's `participants` eids. This also makes a strong demo of "resolve entity names against the graph". It won't always resolve: one sampled transcript lists "Julia Taylor" twice among the attendees. **Link an ambiguous speaker to all candidate eids with an `ambiguous:true` flag rather than guessing.** That is an honest-uncertainty moment worth showing in the demo.<br>(2) PR author/reviewer `login`s are partly mapped: in TrendForce, 31 of 94 PR authors are `eid_`, and 63 are unmapped `EMP_…` IDs. PR links point at real `apache/kafka` PR URLs, so ignore the links.<br>(3) The data uses Salesforce-like product names ("TrendForce") and `sf-internal.slack.com` links. **Rename the company** (for example "Acme Corp") in the UI so no one reads it as real Salesforce internal data. **Rename the org units too**: `employee.org` values are real brand names (`slack`, `tableau`, `mulesoft`, `einstein`, `sales`, `salesforce`).<br>(4) No Slack thread replies in the sampled product.<br>(5) Budget: 1 product is about 1,100 artifacts, which is fine. Do **not** push all 30 through LLM extraction. |
| **EnterpriseRAG-Bench** (HF `onyx-dot-app/EnterpriseRAG-Bench`, arXiv 2605.05253, by Onyx) | 500K+ synthetic docs for a fictional company, "Redwood Inference": Slack 286K, Gmail 121K, Linear 35K, Drive 25K, HubSpot 15K, Fireflies 10K, GitHub 8K, Jira 6K, Confluence 5K. Also 500 questions in categories including **"Conflicting Info" (20)** and **"Info Not Found" (20)**. | **Synthetic, MIT.** Clean. | High realism, but **docs expose only `doc_id`, `source_type`, `title`, `content`**: no structured author, owner or timestamp fields (checked via datasets-server first-rows). Ownership would need LLM extraction. The parquet is **1.4 GB**. | Good **secondary** source. Sample a slice (for example one project's Confluence, Jira and Slack docs), or reuse its "conflicting info" questions as test ideas. |
| **GOV.UK public engineering** (`alphagov/govuk-rfcs`, `alphagov/govuk-developer-docs` → `data/repos.yml`, per-repo `docs/adr` / `docs/arch`) | 102 RFC markdown files with YAML frontmatter (`status: accepted`, `implementation`, `status_last_reviewed`, superseded links). The README process defines decisions as **merged PRs approved by Senior Tech**. `repos.yml` maps **242 repos → owning team** (team = Slack channel name, plus `alerts_team`, type and hosting). ADRs live in about 30 repos (e.g. email-alert-api 10, whitehall 9, govuk-chat 8, publisher 17, content-data-api 15, publishing-api 13). Example chain: **RFC-178 reinstates auto-merge that PR #131 removed, which RFC-167 had allowed.** That chain is a ready-made "decision superseded / knowledge current" demo. | **Public.** `govuk-developer-docs` is MIT. `govuk-rfcs` has **no LICENSE file**, so it is public but unlicensed: Crown copyright, likely OGL (**UNVERIFIED**). Ownership is by team, not person, so no personal data. PR authors are GitHub handles; keep handles only or leave them out. | **High.** Real organization, real decisions, real supersession, no login needed. Accessed through the public GitHub API (`gh api`). | Recommended backup or second graph. Smaller than HERB and with no chats, but real. |
| **Kubernetes KEPs** (`kubernetes/enhancements/keps/sig-*/NNNN-*/kep.yaml` + README) | Structured YAML: `owning-sig`, `participating-sigs`, `authors`, `reviewers`, `approvers` (GitHub handles), `status`, `stage`, `milestone{alpha,beta,stable}`, `latest-milestone`, **`replaces`**, **`see-also`**. SIGs act as teams. | **Public, Apache-2.0.** KEP files hold handles only. **`kubernetes/community/sigs.yaml` contains full names, employers and personal emails**, so strip those fields or don't load them. | High for "who owns what" and decision lineage. Very large (hundreds of KEPs); take one or two SIGs. | Strong structured alternative. Big-name project, which judges recognize. |
| **Python PEPs** (`python/peps/peps/pep-NNNN.rst`) | Headers: `Author`, `Sponsor`, `PEP-Delegate`, `Discussions-To`, `Status`, `Type`, `Created`, `Python-Version`, `Post-History`, **`Resolution` (link to the acceptance post)**, **`Replaces` / `Superseded-By`**. | Public. **Author headers include email addresses**, so strip them. Discourse links are public. | Medium-high (decision tracing). There are no "teams" beyond the Steering Council. | |
| **Rust RFCs + `rust-lang/team`** | `rust-lang/rfcs/text/*.md` (RFC PR #, tracking issue). `rust-lang/team/teams/*.toml` gives **leads, members, alumni per team** (handles) plus rfcbot labels (T-lang). `people/*.toml` holds per-person files. | Public (MIT/Apache). The team repo also lists mailing-list addresses; skip them. `people/*.toml` may contain names and emails, so use handles only. | Medium-high: a real org chart (teams, leads, alumni) plus decisions. | Membership changes are tracked in git history (alumni), which gives a real time dimension. |
| **Backstage ADRs** (`backstage/backstage/docs/architecture-decisions`, adr001–adr015) | 15 ADRs plus CODEOWNERS. | Public, Apache-2.0. | Medium (small; no team model). | Thematic fit: Backstage is itself a software catalog. |
| **TheAgentCompany** (github.com/TheAgentCompany/TheAgentCompany, MIT) | Self-hosted fake software company: GitLab, Plane (issues), ownCloud (docs), RocketChat (chats), all pre-populated. | Synthetic, MIT. | Rich, but needs **30+ GB of Docker** and pre-baked service images. Data extraction is non-trivial. | Too heavy for about 30 h. |
| **EnterpriseBench** (HF `AST-FRI/EnterpriseBench`, Fujitsu, EMNLP 2025, MIT) | 500 enterprise tasks; data across code, HR records, tickets, emails and conversations, persona-based with access control. | Synthetic, MIT. | Medium. Structure not inspected (**UNVERIFIED** whether a usable org chart is included). | |
| **Enron email corpus** | Real emails. | Public, but **real personal data** (names, private emails, personal matters). **Reject** under rule 6. | — | Don't use. |
| **Public mailing-list archives** (Apache lists, LKML) | Threads, votes ([VOTE] = decisions). | Public, but names and email addresses everywhere. **Reject**, or treat as high risk. | — | Apache KIPs on cwiki are public, but the same concern applies. |
| **Our own synthetic generator** | Exactly the schema we want; we can script a "decision reversed" event live. | Synthetic, clean. | Medium (judges may see it as toy data). | Good **complement**. Inject one scripted "new message supersedes old decision" event during the demo. |

**Recommendation:**
- **HERB as the primary source**, with one to three products loaded deterministically.
- LLM extraction only for decisions, claims and expertise in transcripts and docs.
- One scripted live update ("Slack message: we're switching from X to Y") to show supersession.
- **GOV.UK RFCs/ADRs as an optional second graph** (another tenant in the same FalkorDB instance), to show the model generalizes to a real public organization.

---

## 7. Reference products (what judges will compare against)

- **Glean.** Enterprise search plus assistant. Its **Enterprise Graph** (Sept 2025) links people, content, workflows and apps, combined with a per-user **Personal Graph** (May 2025). Agents run on both graphs, with 100+ actions through MCP and strict permission enforcement. https://www.glean.com/press/glean-introduces-third-generation-ai-assistant-new-enterprise-graph-to-enable-the-superintelligent-enterprise
- **Atlassian Rovo.** Search, chat and agents over the **Teamwork Graph**, which links people, goals, work items, code and knowledge across Atlassian and 100+ apps. 2026 additions: a Teamwork Graph CLI, Rovo Studio (no-code agents), and Agents in Jira GA. Sources are secondary coverage; "knowledge owners" is **UNVERIFIED**. https://buzzclan.com/atlassian/atlassian-teamwork-graph-guide/
- **Microsoft GraphRAG** (`graphrag` 3.3.0 on PyPI, 2026-10-08). LLM-extracted entity graph plus Leiden **community summaries**. *Global search* map-reduces over community reports; *local search* walks entity neighborhoods. **LazyGraphRAG** reaches global-search quality at more than 700x lower query cost (Microsoft's figure). It works in static batches and has no temporal model. https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/
- **Zep / Graphiti.** Temporal "context graph" with validity windows and episode provenance; Zep is the managed version.
- **Onyx (formerly Danswer).** Open-source enterprise search and chat with 40+ connectors; publisher of EnterpriseRAG-Bench.
- **"Company brain" category (2026).** The term comes from Foundation Capital's **"Context Graphs: AI's trillion-dollar opportunity"** (Dec 2025). Its thesis is that systems of record capture *what* happened but not *why*, and the opportunity is capturing **decision traces**: inputs, policy, exceptions, approvers. YC's Summer 2026 RFS also mentions the term (secondary source). Startups such as Cerenovus (YC) and Sentra pitch graphs of entities, decisions, commitments and open loops. https://ashugarg.substack.com/p/ais-trillion-dollar-opportunity-context
- **What judges will look for, distilled from the above:**
  - Unified graph across sources.
  - People ↔ expertise ↔ ownership.
  - Decision traces with the "why" and the approver.
  - Freshness/temporal handling.
  - Citations.
  - Permission awareness.
  - Agents that *act*.

  A hackathon entry can't match their connector breadth. It **can** beat them on **visible traversal paths, deterministic supersession, and honest abstention**.

---

## 8. Winning patterns at recent WeMakeDevs hackathons

WeMakeDevs describes itself as a GTM engine for sponsors (https://www.wemakedevs.org/llms.txt): "GitHub stars, API signups, merged pull requests". Deep, visible use of the **sponsor's** product therefore matters.

- **The Hangover Part AI** (Cognee memory hackathon, June/July 2026; 8,500 registrations, 1,200 submissions). Recap: https://www.wemakedevs.org/blogs/hangover-wrap-up-blog
  - **Track 1 winner, "Lethe":** on-call memory for SRE. Messy runbooks become a knowledge graph plus vectors, and answers are **grounded and cited**. It **"can forget systems that have been removed, preventing outdated advice"**. A **live HF Space** and a demo video were provided.
  - **Track 2 winner, "Classroom Memory":** **reasons over prerequisite relationships** in the graph. Instead of "practice recursion", it recommends "teach functions first". This is the graph doing work a flat retriever can't. Live Vercel deployment.
  - An open-source PR track rewarded merged PRs to the sponsor repo (connectors with incremental sync and forget-on-delete, a Slack bot with citations, cost estimators).
- **First Commit** (AWS, Sept 2026, 1,180 projects). Recap: https://www.wemakedevs.org/blogs/first-commit-hackathon-recap-and-whats-next
  - **2nd prize, "Beacon Night Shift":** on-call agent that finds the causing change and proposes **one fix from an allowlist**, **dry-runs it**, then **waits for human approval**.
  - Winners shipped **live links**, not just repos. Blog write-ups earned side prizes.
- **The organizer's FalkorDB blog** (`organizer-blog-blast-radius.txt` in this folder):
  - Fixed tool catalogue of human-written Cypher over MCP.
  - Arguments validated against the graph.
  - `ro_query` for reads.
  - Return `nodes(path)` with every answer.
  - Memory as nodes.
  - Honest "not in the graph".
- **Patterns to copy:**
  1. Cited answers with the traversal path drawn.
  2. Explicit **freshness**: supersede and invalidate stale facts, and show it.
  3. A graph-only capability, such as a multi-hop "who should review this?" or a "decision lineage" chain.
  4. A safe **action** with allowlist, dry-run and approval (e.g. "draft a handoff note to the owner", "open an issue").
  5. Live deployment plus a short video plus a blog post (the AirPods side prize).
  6. Possibly a small upstream PR to a FalkorDB repo (low-cost goodwill; **UNVERIFIED** whether judges weigh it).

---

## 9. FalkorDB server and client versions (pin these)

- **Python client:** `falkordb` **1.8.0** (2026-10-14; `redis>=8,<9`; extra `[hiredis]`).
  - GraphRAG-SDK needs `falkordb>=1.0,<2`.
  - Graphiti needs `>=1.1.2,<2`.
  - QueryWeaver pins `~=1.6.1`.
- **Server:**
  - GitHub releases: v4.22.1 (C engine, 2026-10-15) and v6.0.2 (Rust engine, 2026-10-08).
  - The v6.0.0 notes say `latest` points at 6.0, but **Docker Hub `latest` currently has the same digest as `v4.22.1`** (checked via the Hub API).
  - **Pin `falkordb/falkordb:v4.22.1`.** It bundles the Browser on :3000.
- **CVEs to avoid** (via app.opencve.io, secondary source):
  - CVE-2026-107910: auth bypass via Bolt, before 4.20.0.
  - CVE-2026-88406 and CVE-2026-88407: 4.20.1–4.20.4, fixed in 4.20.5+.
  - CVE-2026-7827 and CVE-2026-5759: RDB loader, fixed in 4.18.4 / 4.18.1.
  - v4.22.1 is past all of these.

---

## 10. Architecture this research points to (option, not decision)

- **Deterministic spine (raw `falkordb` client):**
  - `(:Person {eid})-[:MEMBER_OF]->(:Team)-[:OWNS]->(:Product|:Repo)`
  - `(:Person)-[:REPORTS_TO]->(:Person)`
  - `(:Message|:Document|:Meeting|:PR)-[:IN_CHANNEL|:ABOUT]->(:Product)`
  - `(:Person)-[:AUTHORED|:ATTENDED|:REVIEWED]->(...)`
- **LLM extraction layer** (structured output, small model):
  - `(:Decision {text, decided_at, status})-[:DECIDED_IN]->(:Meeting|:Message|:Document)`
  - `(:Decision)-[:DECIDED_BY]->(:Person)`
  - `(:Decision)-[:SUPERSEDES {at}]->(:Decision)`
  - `(:Person)-[:KNOWS_ABOUT {evidence_ids, weight}]->(:Topic)`
  - Facts carry `valid_from` / `valid_to`.
  - Every extracted node keeps `source_ids` and links to its source artifact node.
- **Agent tools** (fixed Cypher over MCP):
  - `resolve_entity(name, type)`, using full-text index plus jaroWinkler (FLEX UDF if it loads, otherwise Python-side) and returning candidates with eids. Ambiguous names return several candidates rather than a guess.
  - `who_owns(thing)`, `who_knows(topic)` (PageRank-weighted), `decision_lineage(decision)` (path through `SUPERSEDES`, back to the source message).
  - `current_decision(topic, as_of)`, `what_changed_since(date)`, `find_expert_path(personA, topic)`.
  - `record_memory(...)`: agent conclusions stored as nodes with provenance.
  - Every tool returns `nodes(path)` plus source IDs, and returns "NOT_IN_GRAPH" explicitly when there are no rows.
- **Eval:** HERB `answerable_questions` (score our citations against theirs) plus `unanswerable_questions` (abstention rate).

---

## Sources

**GraphRAG-SDK**
- README: https://github.com/FalkorDB/GraphRAG-SDK
- PyPI: https://pypi.org/project/graphrag-sdk/
- Docs: https://docs.falkordb.com/graphrag
- Architecture: https://docs.falkordb.com/graphrag/architecture
- Providers: https://docs.falkordb.com/graphrag/providers
- Extraction: https://docs.falkordb.com/graphrag/extraction
- Ontology discovery: https://docs.falkordb.com/graphrag/ontology-discovery
- Structured ingestion: https://docs.falkordb.com/graphrag/structured-ingestion
- Benchmark (indexing time): https://docs.falkordb.com/graphrag/benchmark
- Doc sources: https://github.com/falkordb/docs (branch `docs-site`)

**Graphiti**
- Repo: https://github.com/getzep/graphiti
- PyPI: https://pypi.org/project/graphiti-core/
- `graphiti_core/edges.py` and `graphiti_core/graphiti.py` (main)
- FalkorDB quickstart: `examples/quickstart/quickstart_falkordb.py`
- MCP server: `mcp_server/README.md`
- FalkorDB issues: https://github.com/getzep/graphiti/issues?q=falkordb (issues #1842, #1876, #1934, #1885, #1991, #1911)
- Zep paper: https://arxiv.org/abs/2501.13956
- Performance mirror (UNVERIFIED call counts): https://mintlify.com/getzep/graphiti/advanced/performance-tuning

**Other frameworks**
- Cognee: https://github.com/topoteretes/cognee, https://docs.falkordb.com/agentic-memory/cognee, https://pypi.org/project/cognee-community-hybrid-adapter-falkor/
- QueryWeaver: https://github.com/FalkorDB/QueryWeaver, https://docs.falkordb.com/genai-tools/queryweaver
- LangChain: https://docs.falkordb.com/genai-tools/langchain, https://pypi.org/project/langchain-falkordb/
- LlamaIndex: https://docs.falkordb.com/genai-tools/llamaindex, https://pypi.org/project/llama-index-graph-stores-falkordb/
- MCP server: https://docs.falkordb.com/genai-tools/mcpserver, https://github.com/FalkorDB/FalkorDB-MCPServer
- Graphify: https://docs.falkordb.com/genai-tools/graphify
- Integrations: https://docs.falkordb.com/integration/
- Community projects: https://www.falkordb.com/community-projects/
- Code-graph: https://github.com/FalkorDB/code-graph, https://docs.falkordb.com/genai-tools/code-graph

**FalkorDB server**
- Releases: https://github.com/FalkorDB/FalkorDB/releases
- Docker Hub tags: https://hub.docker.com/r/falkordb/falkordb/tags
- CVE: https://app.opencve.io/cve/CVE-2026-107910

**Data**
- HERB: https://huggingface.co/datasets/Salesforce/HERB, https://arxiv.org/abs/2506.23139
- EnterpriseRAG-Bench: https://huggingface.co/datasets/onyx-dot-app/EnterpriseRAG-Bench, https://arxiv.org/abs/2605.05253
- EnterpriseBench: https://huggingface.co/datasets/AST-FRI/EnterpriseBench, https://arxiv.org/abs/2510.27287
- TheAgentCompany: https://github.com/TheAgentCompany/TheAgentCompany
- GOV.UK: https://github.com/alphagov/govuk-rfcs, https://github.com/alphagov/govuk-developer-docs/blob/main/data/repos.yml
- Kubernetes: https://github.com/kubernetes/enhancements, https://github.com/kubernetes/community/blob/master/sigs.yaml
- Python PEPs: https://github.com/python/peps
- Rust: https://github.com/rust-lang/rfcs, https://github.com/rust-lang/team
- Backstage ADRs: https://github.com/backstage/backstage/tree/master/docs/architecture-decisions

**Reference products**
- Glean (see section 7)
- Atlassian (secondary coverage, see section 7)
- Microsoft GraphRAG: https://github.com/microsoft/graphrag, https://pypi.org/project/graphrag/, LazyGraphRAG blog
- Foundation Capital context graphs: https://ashugarg.substack.com/p/ais-trillion-dollar-opportunity-context

**WeMakeDevs**
- https://www.wemakedevs.org/blogs/hangover-wrap-up-blog
- https://www.wemakedevs.org/blogs/first-commit-hackathon-recap-and-whats-next
- https://www.wemakedevs.org/blogs/how-to-build-an-agent-that-reasons-over-a-graph
- https://www.wemakedevs.org/llms.txt
