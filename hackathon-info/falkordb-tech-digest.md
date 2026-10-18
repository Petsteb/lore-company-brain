# FalkorDB Technical Digest for Graph Hacks 2026 (Track 03, "Company Brain")

Compiled 2026-10-17. Sources:
- **Docs:** source markdown from `github.com/FalkorDB/docs`, commit `f804fd7` (2026-10-15).
- **Engine source:** `github.com/FalkorDB/FalkorDB` at tags `v6.0.2` (Rust engine, which Docker `latest` points to) and `v4.22.1` (C engine).
- **Client source:** `falkordb-py` main, `falkordb-ts` main and `FalkorDB-MCPServer` main.
- **Registry metadata:** PyPI, npm, GitHub releases.
- **Cloud:** docs.falkordb.com/cloud/tiers/free.md and falkordb.com/pricing.

Labels used in this file:
- **[SRC]**: verified in engine or client source code.
- **[DOCS]**: stated in the docs only.
- **UNVERIFIED**: not confirmed. Test it before relying on it.
- **DOC-BUG**: the docs contradict the source.

---

## 0. TL;DR: the facts that change decisions

1. **`falkordb/falkordb:latest` is FalkorDB 6.0, a new Rust engine released 2026-09-29.** v6.0.2 shipped 2026-10-08. The C engine continues as 4.22.x (v4.22.1, 2026-10-15). The `GRAPH.*` commands, Cypher and clients are the same in both. [SRC: v6.0.0 release notes]
2. **Several algorithms in the docs are not in `latest`.**
   - **Not in 6.0.2:** `algo.AStar` and the CCH index (`CREATE CCH INDEX`, `db.idx.cch.query`). Both are in **4.22.x only**. Leiden and Louvain are in no release.
   - **In 6.0.2:** `algo.pageRank`, `algo.BFS`, `algo.betweenness`, `algo.WCC`, `algo.labelPropagation`, `algo.MSF`, `algo.SPpaths`, `algo.SSpaths`, `algo.HarmonicCentrality`, `algo.maxFlow`. [SRC: grep of both tags]
   - **First command on any instance, local or Cloud:** `CALL dbms.procedures()`.
3. **Vector search: fully supported.**
   - **Index:** HNSW, created with `CREATE VECTOR INDEX FOR (n:L) ON (n.emb) OPTIONS {dimension:1536, similarityFunction:'cosine'}`.
   - **Values:** store vectors with `vecf32([...])`.
   - **Query:** `CALL db.idx.vector.queryNodes('L','emb',k,vecf32($q)) YIELD node, score`.
   - **Score is a distance. Lower means closer, for both cosine (1 − cos_sim) and euclidean.** Sort ascending. The cheat-sheet's "cosine → ORDER BY score DESC" is a DOC-BUG. [SRC]
   - **No pre-filtering.** Over-fetch `k`, then filter with `WHERE`.
4. **Full-text search: supported, using the RediSearch engine.**
   - **Create:** `CALL db.idx.fulltext.createNodeIndex('Doc','title','body')` or `CREATE FULLTEXT INDEX FOR (d:Doc) ON (d.body)`.
   - **Query:** `CALL db.idx.fulltext.queryNodes('Doc', $q) YIELD node, score`. Score is TF-IDF, higher is better.
   - **Query syntax is RediSearch's.** `-`, `|`, `%`, `*` and `@` are operators, so sanitize user text.
5. **Temporal types are local only.**
   - **Supported:** `date()`, `localtime()`, `localdatetime()`, `duration()`, `timestamp()` (epoch ms), and the `.transaction` variants.
   - **Missing from 6.0.2:** `datetime()` (zoned) and `time()` (zoned). [SRC]
   - **Precision:** values are stored as epoch seconds, with no sub-second precision. [SRC]
   - **Indexing:** range indexes do not cover temporals [DOCS]. The range-index page lists only strings, numbers, points and arrays. 6.0.2 also stopped temporals matching numeric range predicates.
   - **Recommendation:** store `*_ts` epoch-millisecond integers for anything you filter or sort on. Keep ISO strings or `localdatetime` only for display.
6. **Property-value restrictions.**
   - **Maps cannot be stored as properties.** Flatten them, or store a JSON string.
   - **Arrays are OK** if they contain no null or graph entities.
   - **null cannot be stored.** `SET x = null` removes the property.
7. **Parameters.**
   - Work in WHERE/property maps, `SKIP $s LIMIT $l` (contrary to the MCP README), list slices, and procedure args (`vecf32($q)`). [SRC tests]
   - **Do not work in variable-length bounds.** `[*1..$d]` is a parse error because the parser only accepts integer literals. Interpolate a validated int instead. [SRC parser]
8. **Docker image defaults that bite.**
   - **Default env:** `FALKORDB_ARGS="MAX_QUEUED_QUERIES 25 TIMEOUT 1000 RESULTSET_SIZE 10000"`. Read queries time out after **1 s**, and results are **silently truncated at 10,000 rows**. Setting your own `FALKORDB_ARGS` replaces all three values.
   - **Data dir:** the real path is `/var/lib/falkordb/data`. `/data` is a symlink to it, and `--dir` is forced after `REDIS_ARGS`. [SRC Dockerfile/run.sh]
9. **Cloud free tier: do not use it for the judged live demo.**
   - 100 MB RAM total.
   - No TLS, no persistence, no backups.
   - Stopped after 1 day unused and deleted after 7 days. [Cloud docs]
   - Self-host Docker with a volume instead, or pay for Startup (from $73/month, 1 GB, TLS, 12-hour backups).
10. **FalkorDB MCP server** (`@falkordb/mcpserver` 1.3.0, image `falkordb/mcpserver`). It has 7 tools: `query_graph`, `query_graph_readonly`, `list_graphs`, `delete_graph`, `get_graph_schema`, `get_node_schema` and `get_relationship_schema`. It runs over stdio or Streamable HTTP, with optional Bearer API key.
11. **Known limitations (official):**
    - Unreferenced edges are de-duplicated in matching.
    - LIMIT does not stop eager writes.
    - Indexes ignore `<>`.
    - No aggregates inside pattern comprehensions.
    - Docs also say: no label expressions `(n:A|B)` and no `exists((n)-->())`. Use pattern predicates in WHERE.
12. **JavaScript UDFs exist** (`GRAPH.UDF LOAD`), but they **cannot modify the graph**. The cypher-support page says "UDFs unsupported", which is a DOC-BUG and outdated.

---

## 0.5 Version reality check (do this first on any instance)

```cypher
CALL dbms.procedures()            // which algo.* / db.idx.* exist on THIS server
CALL dbms.functions()             // exists in 6.0.2 source [SRC]; UNVERIFIED in 4.x docs
```
```bash
redis-cli GRAPH.CONFIG GET '*'    # check TIMEOUT / TIMEOUT_DEFAULT / RESULTSET_SIZE actually in force
redis-cli INFO modules            # module version
```

| Release | Date | Engine | Docker tags |
|---|---|---|---|
| v6.0.2 | 2026-10-08 | Rust | `latest`, `6.0.x`, `edge` (amd64 + arm64). No Alpine build. |
| v4.22.1 | 2026-10-15 | C | `v4.22.1`; `edge-c` |
| v4.20.7 | 2026-09-24 | C | `latest-alpine` stays on 4.20.7 |

v6.0 notes:
- 6.0 uses RediSearch 8.6.1, up from 2.6.23. Full-text scoring still defaults to TF-IDF.
- 4.x and 6.0 nodes cannot be mixed in one replication set.
- **Pick an engine:** use `latest` (6.0.2) unless you need A* or CCH. For those, pin `falkordb/falkordb:v4.22.1`. Do not float on `latest` during the hackathon; pin a tag.

Sources: https://github.com/FalkorDB/FalkorDB/releases (v6.0.0, v6.0.2, v4.22.0).

---

## 1. Getting started, Docker, ports, persistence

Sources:
- https://docs.falkordb.com/getting-started/
- https://docs.falkordb.com/operations/docker
- FalkorDB `build/runtime/Dockerfile` and `run.sh` @v6.0.2

### Images

| Image | Contents | Ports |
|---|---|---|
| `falkordb/falkordb` | server + Browser UI | 6379 (Redis/RESP), 3000 (Browser) |
| `falkordb/falkordb-server` | server only | 6379 |
| `falkordb/falkordb-browser` | Browser only; needs `FALKORDB_URL=redis://host:6379` (+ `FALKORDB_PASSWORD`) | 3000 |

- **Redis:** FalkorDB requires Redis ≥ 8.0. The images bundle Redis 8.10.2.
- **Bolt:** optional, off by default (`BOLT_PORT -1`). Enable it with `FALKORDB_ARGS="BOLT_PORT 7687"`.

### Quick start

```bash
docker run -p 6379:6379 -p 3000:3000 -it --rm falkordb/falkordb:latest          # no auth
docker run -p 6379:6379 -p 3000:3000 -it --rm \
  -e REDIS_ARGS="--requirepass yourpassword" falkordb/falkordb:latest           # with password
```

### What the entrypoint actually does [SRC run.sh @v6.0.2]

```sh
redis-server ${REDIS_ARGS} --protected-mode no --dir "${FALKORDB_DATA_PATH}" \
  --loadmodule .../falkordb.so ${FALKORDB_ARGS}
```

- **Data path:** `FALKORDB_DATA_PATH=/var/lib/falkordb/data`. `/data` is a symlink to it.
  - `--dir` comes after `REDIS_ARGS`, so you cannot move the data dir with `REDIS_ARGS`.
  - **Mount the volume at `/var/lib/falkordb/data`.** The docs FAQ uses this path. Their compose examples mount `/data`, which only works if Docker follows the symlink. That is UNVERIFIED, and the MCP-server repo reports data loss with a `/data` mount.
- **Default env (image):** `FALKORDB_ARGS="MAX_QUEUED_QUERIES 25 TIMEOUT 1000 RESULTSET_SIZE 10000"`, `TLS=0`.
  - `BROWSER=1` in `falkordb/falkordb`.
  - If you pass `-e FALKORDB_ARGS="THREAD_COUNT 4"`, the TIMEOUT 1000, MAX_QUEUED 25 and RESULTSET 10000 defaults are dropped. The engine defaults then apply: TIMEOUT off and RESULTSET_SIZE 10,000 per the config docs.
- **Non-root since 6.0.2:** redis-server runs as user `falkordb` via gosu.
  - The entrypoint chowns the data dir only if it contains just `*.rdb`, `nodes*.conf` or `appendonlydir`. Otherwise it stays root.
  - `SKIP_DROP_PRIVS=1` keeps root. `SKIP_FIX_PERMS=1` skips the chown.
- **TLS:** `TLS=1` generates self-signed certs (`gen-certs.sh`) and serves TLS on 6379 with `--tls-auth-clients no`.

### Recommended hackathon compose (pinned tag, persistence, sane limits)

```yaml
services:
  falkordb:
    image: falkordb/falkordb:6.0.2        # tags verified on Docker Hub: 6.x has NO "v" prefix; 4.x does -> falkordb/falkordb:v4.22.1 (needed for algo.AStar / CCH)
    ports: ["6379:6379", "3000:3000"]
    environment:
      - REDIS_ARGS=--requirepass ${FALKORDB_PASSWORD:?set FALKORDB_PASSWORD} --appendonly yes --appendfsync everysec
      - FALKORDB_ARGS=THREAD_COUNT 4 TIMEOUT_MAX 30000 TIMEOUT_DEFAULT 15000 RESULTSET_SIZE 50000 QUERY_MEM_CAPACITY 536870912
    volumes:
      - falkordb_data:/var/lib/falkordb/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${FALKORDB_PASSWORD:?set FALKORDB_PASSWORD}", "ping"]
      interval: 30s
      timeout: 10s
      retries: 3
volumes:
  falkordb_data:
```

Notes on this compose file:
- **Tags** (checked on Docker Hub 2026-10-17):
  - `falkordb/falkordb` and `falkordb/falkordb-server` both have `6.0.0`, `6.0.1`, `6.0.2`, `v4.22.0` and `v4.22.1`.
  - 4.22.x also has `-alpine`, `-arm64v8`, `-x64` and `-rhel*` variants.
- **v4 images** use the same data path (`${FALKORDB_HOME}/data` with a `/data` symlink) and the same default `FALKORDB_ARGS`.
- **Password guard:** `${FALKORDB_PASSWORD:?...}` makes compose fail loudly if the password is unset. Otherwise `--requirepass` would get no value and redis-server would fail to start.
- **Browser exposure:** for production the docs suggest `falkordb-server` with no Browser. For the demo you probably want the Browser, but don't expose 3000 publicly without auth.

### Persistence

- AOF: `--appendonly yes`.
- RDB: Redis default save rules apply unless overridden. That is UNVERIFIED for this image's redis.conf.
- `GRAPH.COPY` dumps go to `TEMP_FOLDER` (default `/tmp`).

---

## 2. Clients

### Versions (checked 2026-10-17)

| Package | Latest | Date | Notes |
|---|---|---|---|
| PyPI `falkordb` | **1.8.0** | 2026-10-14 | requires Python ≥ 3.10; `pip install "FalkorDB[hiredis]"` for faster parsing of big results/vectors |
| npm `falkordb` | **6.8.0** | 2026-08-25 | built on node-redis; temporals via `@js-temporal/polyfill` |
| npm `@falkordb/mcpserver` | **1.3.0** | 2026-07-01 | see §8 |

Other official clients: Java `jfalkordb`, Rust `falkordb`, Go `falkordb-go`, PHP, C# `NFalkorDB`. OGMs exist: `falkordb-py-orm`, `falkordb-go-orm`, `spring-data-falkordb`.

### Python `falkordb` [SRC falkordb-py]

```python
from falkordb import FalkorDB
db = FalkorDB(host="localhost", port=6379, username=None, password=None,
              ssl=False)                     # redis-py style kwargs; ssl_ca_certs=..., socket_timeout=..., etc.
db = FalkorDB.from_url("falkor://user:pass@host:6379")   # falkors:// = TLS (mapped to rediss://)

g = db.select_graph("brain")                 # graph = Redis key; created on first write
res = g.query("MATCH (p:Person {id:$id}) RETURN p", params={"id": "u1"}, timeout=5000)  # timeout ms
res = g.ro_query("MATCH (n) RETURN count(n)")             # GRAPH.RO_QUERY: errors on writes
res.header        # column descriptors
res.result_set    # list of rows; each row is a list (positional, NOT dict)
res.nodes_created, res.relationships_created, res.properties_set, res.run_time_ms, res.cached_execution
db.list_graphs()  # GRAPH.LIST
g.copy("brain_snapshot"); g.delete()
g.explain(q, params) / g.profile(q, params)   # ExecutionPlan objects
g.slowlog(); g.slowlog_reset()
db.config_get("RESULTSET_SIZE"); db.config_set("TIMEOUT_DEFAULT", 10000)
db.udf_load("Lib", js_source, replace=True); db.udf_list(); db.udf_delete("Lib"); db.udf_flush()
```

**Index and constraint helpers** (methods on the graph object `g`):
- `g.create_node_range_index(label, *props)`, `g.create_node_fulltext_index(label, *props)`.
- `g.create_node_vector_index(label, *props, dim=1536, similarity_function="cosine")`.
- `create_edge_*` variants, `drop_node_*_index`, `g.list_indices()`.
- **Constraints:** `g.create_node_unique_constraint(label, *props)` auto-creates any missing range index. Also `create_node_mandatory_constraint`, `create_edge_unique_constraint`, `create_edge_mandatory_constraint`, the `drop_*` variants and `list_constraints()`.
- **DOC-BUG:** the docs' `client.create_constraint('g','UNIQUE',...)` is not a method on `FalkorDB` in the current source. Use the graph methods above.

**Result objects** (`falkordb.node.Node`, `falkordb.edge.Edge`, `falkordb.path.Path`):
- `Node`: `.id`, `.labels` (list), `.properties` (dict).
- `Edge`: `.id`, `.relation` (type), `.src_node`, `.dest_node` (node ids or Nodes), `.properties`.
- `Path`: `.nodes()`, `.edges()`, `.get_node(i)`, `.first_node()`, `.last_node()`, `.node_count()`, `.edge_count()`.
- **Temporal:** returned as Python `datetime` / `date` / `time` (UTC) and `relativedelta` for durations.
- **Vectors:** `vecf32` comes back as a list of floats.
- **Maps:** dicts.

**How params are sent:** the client serializes `params` into a `CYPHER k=v ...` text prefix.
- Values must be JSON-like: str, int, float, bool, None, list or dict.
- **Python `datetime` objects are not valid params.** Pass ISO strings and wrap them: `localdatetime($ts)`, or pass epoch ints.
- A 1536-float list param works, but it is sent as text. Batch writes to amortize the cost.

**Async API:**
```python
from falkordb.asyncio import FalkorDB
from redis.asyncio import BlockingConnectionPool
pool = BlockingConnectionPool(max_connections=16, timeout=None, decode_responses=True)
db = FalkorDB(connection_pool=pool)
g = db.select_graph("brain")
res = await g.query("MATCH (n) RETURN n LIMIT 10")
await pool.aclose()
```

### Node.js / TypeScript `falkordb` [SRC falkordb-ts]

```ts
import { FalkorDB } from 'falkordb';
const db = await FalkorDB.connect({ host: 'localhost', port: 6379, username: 'u', password: 'p' });
// or: FalkorDB.connect({ url: 'falkor://localhost:6379' })
// TLS / timeouts: FalkorDB.connect({ socket: { host, port, tls: true, connectTimeout: 5000 }, password })
const graph = db.selectGraph('brain');
const r = await graph.query(`MATCH (p:Person) WHERE p.name = $name RETURN p.name AS name`,
                            { params: { name: 'Alice' }, TIMEOUT: 5000 });
r.data   // Array<Record<alias, value>>  e.g. r.data[0].name
await graph.roQuery(q, { params });
await graph.explain(q); await graph.profile(q); await graph.copy('dst'); await graph.delete();
await db.list(); await db.info(); await db.close();
```

- **Value shapes:**
  - `GraphNode {id, labels[], properties}`
  - `GraphEdge {id, relationshipType, sourceId, destinationId, properties}`
  - `GraphPath {nodes[], edges[]}`
  - Temporals come back as `Temporal.PlainDateTime | PlainDate | PlainTime | Duration`.
- **Index helpers:** `createNodeRangeIndex`, `createNodeFulltextIndex`, `createNodeVectorIndex`, plus `constraintCreate`.
- **DOC-BUG:** the getting-started JS snippet does `for (const record of result)`. You must iterate `result.data`.

Sources:
- https://docs.falkordb.com/getting-started/clients
- https://github.com/FalkorDB/falkordb-py
- https://github.com/FalkorDB/falkordb-ts
- https://pypi.org/project/falkordb/
- https://www.npmjs.com/package/falkordb

---

## 3. Cypher in FalkorDB

Sources:
- https://docs.falkordb.com/cypher/
- `/cypher/cypher-support`, `/cypher/known-limitations`, `/cypher/functions`, `/datatypes`, `/cypher/cheat-sheet`
- 6.0.2 parser and function registry

### Supported clauses
- **Read:** `MATCH`, `OPTIONAL MATCH`, `WHERE`, `RETURN [DISTINCT]`, `WITH`, `UNWIND`, `ORDER BY`, `SKIP`, `LIMIT`, `UNION` / `UNION ALL`.
- **Write:** `CREATE`, `MERGE` (+ `ON CREATE SET` / `ON MATCH SET`), `SET` (`=`, `+=`, labels), `REMOVE` (props, labels), `DELETE` / `DETACH DELETE`, `FOREACH`.
  - `DELETE` of a node always removes its edges, so it behaves like DETACH.
- **Other:** `CALL proc() YIELD ...`, `CALL { subquery }`, `LOAD CSV [WITH HEADERS] FROM 'file://…' | 'https://…' AS row [FIELDTERMINATOR ';']`.
- **Index/constraint DDL** inside GRAPH.QUERY: `CREATE INDEX`, `CREATE FULLTEXT INDEX`, `CREATE VECTOR INDEX`, `DROP ...`.
  - Constraints are **not** Cypher DDL. They use the `GRAPH.CONSTRAINT` command.

### Key clause semantics

**`CALL {}` subqueries:**
- Run once per input row.
- Import outer vars **only** via an opening `WITH a, b` or `WITH *`.
- Returned vars can't shadow outer ones.
- A non-returning subquery (side effects only) keeps the row count.
- Post-UNION processing works.

```cypher
MATCH (d:Decision)
CALL { WITH d MATCH (d)<-[:SUPERSEDES]-(n:Decision) RETURN count(n) AS newer }
RETURN d.title, newer
```

**`FOREACH`:**
- The body may contain only update clauses: CREATE, MERGE, SET, REMOVE, DELETE, FOREACH.
- Conditional-write idiom: `FOREACH (_ IN CASE WHEN x IS NULL THEN [1] ELSE [] END | MERGE ...)`.

**`MERGE`** matches or creates the **whole** pattern:
- Merge nodes first, then the edge: `MERGE (a:P {id:$a}) MERGE (b:P {id:$b}) MERGE (a)-[:KNOWS]->(b)`.
- Bound aliases are only matched, never created.
- Variable-length patterns are not allowed in CREATE/MERGE. [SRC]

**`OPTIONAL MATCH`:**
- A mandatory MATCH cannot follow an OPTIONAL MATCH. [DOCS]
- Creating an edge to a null endpoint errors, and the whole query rolls back.

**`WITH`:** a `WHERE` placed after `WITH` sees the post-write graph state.

**Variable length:** `-[:T*1..3]->`, `-[*]->` (1..∞), `-[*2]->` (exactly 2).
- **Bounds must be integer literals.** Parameters are rejected. [SRC]
- Relationship type alternation works: `[:A|B]` or `[:A|:B]`.
- Undirected patterns: `-[:T]-`.
- Use `prev()` to filter on the previous hop's value, e.g. `WHERE coalesce(prev(s.priority), s.priority) <= s.priority`.

**`shortestPath` / `allShortestPaths`:**
- Both endpoints must be bound beforehand.
- **No property filters inside the pattern.**
- Minimum hops may be 0 or 1 only.
- Returns NULL if no path exists.
- Hop count only, unweighted. For weights use `algo.SPpaths`.

Undirected `shortestPath((a)-[...]-(b))` is UNVERIFIED. Every docs and test example is directed (`->` or `<-`). If the undirected form fails, use `algo.SPpaths` with `relDirection:'both'`.

```cypher
MATCH (a:Person {id:$a}), (b:Person {id:$b})
WITH shortestPath((a)-[:KNOWS|WORKS_WITH*]-(b)) AS p   // undirected: UNVERIFIED
RETURN length(p), [n IN nodes(p) | n.name]
MATCH (a:City{name:'A'}),(g:City{name:'G'}) WITH a,g
MATCH p=allShortestPaths((a)-[*]->(g)) RETURN p
```

**Expressions:**
- **Comprehensions and folds:** list comprehension `[x IN list WHERE cond | expr]`, pattern comprehension `[(n)-[:R]->(m) WHERE ... | m.name]`, `reduce(acc=0, x IN l | acc+x)`.
- **Predicates:** `any`, `all`, `none`, `single`, `CASE`.
- **Maps:** map projection `n {.name, .id, extra: x}`, map merge `{a:1} + {b:2}`.
- **Path functions:** `nodes(p)`, `relationships(p)`, `length(p)`.

**Pattern predicates in WHERE** work: `WHERE NOT (p)-[:OWNS]->(:Doc)`. `exists((n)-->())` does **not** work; use the pattern directly.

### Functions worth knowing

FalkorDB-specific functions are marked `*`.

- **Scalar:** `coalesce`, `id`, `labels`, `type`, `properties`, `keys`, `startNode`, `endNode`, `timestamp()` (epoch ms), `randomUUID()`.
  - Also: `typeOf()*`, `hasLabels(n,[...])*`, `prev()*`, `indegree(n,'T')*`, `outdegree(n,'T')*`, `intern(str)*` (dedups repeated strings in memory).
- **Lists:** `head`, `last`, `tail`, `size`, `range`, `list.dedup*`, `list.sort*`, `list.insert*`, `list.insertListElements*`, `list.remove*`.
- **Strings:** `toLower`, `toUpper`, `trim`, `lTrim`, `rTrim`, `left`, `right`, `substring`, `split`, `replace`, `reverse`.
  - Also: `string.join*`, `string.matchRegEx*`, `string.replaceRegEx*`, `toJSON*`.
- **Aggregates:** `count`, `collect`, `sum`, `avg`, `min`, `max`, `stDev`, `stDevP`, `percentileCont`, `percentileDisc`.
- **Type conversion:** `toInteger`, `toFloat`, `toString`, `toBoolean`, the `*OrNull` variants and the `*List` variants.
- **Point:** `point({latitude, longitude})`, `distance(p1,p2)` (meters).
- **Vector:** `vecf32(list)`, `vec.cosineDistance(a,b)`, `vec.euclideanDistance(a,b)`.
  - The FAQ mentions `vecf64()`, but it is **not registered in 6.0.2**. [SRC]
- **Regex `=~`:** the docs say it is unsupported, but the 6.0.2 lexer, parser and runtime implement it (`regex_matches`). Prefer `string.matchRegEx()` for portability. Treat `=~` as UNVERIFIED on 4.x.

### Data types and storage rules [DOCS + SRC]

| Type | Storable as property? | Notes |
|---|---|---|
| Integer (i64), Float (f64), String, Boolean | yes | `1 = true` is **false** (types compared) |
| Point | yes | 32-bit floats lat/lon |
| Array of scalars | yes | no nulls/entities inside; range-indexable for `x IN arr` |
| Vector (`vecf32`) | yes | required for vector index |
| Date / LocalTime / LocalDateTime / Duration | yes | stored as epoch seconds; no zone; no sub-second |
| **Map** | **NO** | flatten or `toJSON()`-style string |
| null | no | `SET n.x = null` removes property |
| Node/Edge/Path | no | |

### Temporal support in detail [SRC 6.0.2 `temporal.rs`]

**Constructors:**
- `date('2025-09-15')` or `date({year:2025, month:9, day:15})`; `date()` returns today.
- `localtime('07:00:00')`, `localdatetime('2025-06-29T13:45:00')`; `localdatetime()` returns now (UTC clock).
- `duration('P3DT12H')` or `duration({days:3})`.
- `date.transaction()`, `localdatetime.transaction()`, `localtime.transaction()`.
- `timestamp()` returns epoch **milliseconds** as an Int.

**Not present:** `datetime()` and `time()` (zoned), and `duration.between()`. The FLEX UDF docs and the Graphiti page use `datetime(...)`, which will fail on 6.0.2 core unless FLEX is loaded. UNVERIFIED whether FLEX provides it.

**Supported operations:**
- Comparison with `< > =`.
- Component access: `.year`, `.month`, `.day`, `.hour`, `.minute`, `.second`; on durations `.years`, `.hours`, etc.
- Arithmetic: `date + duration`, `localdatetime - duration`, `duration + duration`.
- cypher-support.mdx says "temporal arithmetic functions unsupported", which contradicts datatypes.mdx. The `+`/`-` operator forms are documented with examples. Test them.

**Recommendation:** for validity windows and recency ranking, store `valid_from_ts` / `valid_to_ts` / `updated_ts` as **epoch-ms ints**, which are range-indexable. Optionally also store `localdatetime` for display.

### Known limitations: the full official list (paraphrased closely) [DOCS /cypher/known-limitations]

1. **Relationship uniqueness in patterns.**
   - If a relationship in a MATCH pattern is not referenced elsewhere, FalkorDB only verifies that **at least one** matching relation exists.
   - Example: two parallel edges a→b. `MATCH (a)-[e]->(b) RETURN count(e)` gives 2, but `MATCH (a)-[e]->(b) RETURN count(b)` gives **1**.
   - Workaround: reference the alias, e.g. `WHERE ID(e) >= 0`, or return `e.dummyval`.
   - **This matters for "count mentions or messages between people" queries.**
2. **LIMIT does not affect eager operations.**
   - CREATE, SET, DELETE, MERGE and aggregating projections run fully before LIMIT.
   - Example: `UNWIND [1,2,3] AS v CREATE (a {property:v}) RETURN a LIMIT 1` creates 3 nodes.
3. **Indexes do not handle `<>` filters.** Indexes are used for equality, ranges and string prefix only. Check with `GRAPH.EXPLAIN`.
4. **No aggregation functions inside pattern comprehensions**, neither in the expression nor in the embedded WHERE.
   - This errors: `Invalid use of aggregating function 'count'`.
   - Aggregate in a preceding WITH, or use `size([...])`.

**Also documented as unsupported** [cypher-support / functions / cheat-sheet]:
- Label expressions such as `(n:A|B)` in node patterns. Use `WHERE n:A OR n:B`.
- Hexadecimal and octal literals.
- Pattern-form `exists()`.
- Schema-retrieval queries. Use `db.labels()`, `db.relationshipTypes()`, `db.propertyKeys()`, `db.meta.stats()`.
- Vector queries combined with property filters.
- Geospatial index use only for `<` / `<=` distance filters.

---

## 4. Indexes and constraints (hybrid retrieval)

Sources:
- https://docs.falkordb.com/cypher/indexing/ (range-index, fulltext-index, vector-index, cch-index)
- `/commands/graph.constraint-create`, `/cypher/procedures`

### Range (exact-match) index
```cypher
CREATE INDEX FOR (p:Person) ON (p.id)                     // node
CREATE INDEX FOR (p:Person) ON (p.first_name, p.last_name)// multi-prop (used by composite UNIQUE)
CREATE INDEX FOR ()-[r:MENTIONS]-() ON (r.ts)             // relationship
CREATE INDEX ON :Person(age)                              // legacy syntax
DROP INDEX ON :Person(age)
CALL db.indexes()   // YIELD label, properties, types, options, language, stopwords, entitytype, status, info
```

- **Indexable types:** strings, numbers, points, and arrays of scalars (`WHERE 90 IN p.samples`).
- **Not indexable:** maps, vectors, nested arrays, and (in effect) temporals.
- Indexes are used automatically by `=`, ranges, `STARTS WITH` and `IN`. They are not used for `<>`.
- **Index every `id` you MERGE on.** Without an index, MERGE does a label scan for every row.

### Full-text index (RediSearch)

```cypher
CALL db.idx.fulltext.createNodeIndex('Doc', 'title', 'body')                 // multi-prop
CALL db.idx.fulltext.createNodeIndex({label:'Doc', language:'English', stopwords:['the']}, 'body')
CALL db.idx.fulltext.createNodeIndex('Person', {field:'name', phonetic:'dm:en'})   // also: weight, nostem
CREATE FULLTEXT INDEX FOR (d:Doc) ON (d.body) [OPTIONS {language:'English', stopwords:[...]}]
CREATE FULLTEXT INDEX FOR ()-[m:MESSAGE]-() ON (m.text)                      // relationships
CALL db.idx.fulltext.queryNodes('Doc', $q) YIELD node, score                 // score = TF-IDF (higher=better)
CALL db.idx.fulltext.queryRelationships('MESSAGE', $q) YIELD relationship, score
CALL db.idx.fulltext.drop('Doc')                  // drops ALL full-text fields on label
DROP FULLTEXT INDEX FOR (d:Doc) ON (d.body)       // drops one field
```

**Query syntax** (RediSearch):
- `word1 word2` means AND; `a|b` means OR; `-word` means NOT.
- `pref*` is a prefix match, at the end of a word only.
- `%term%1` is fuzzy (Levenshtein distance 1–3).
- Stemming is per language.
- **Sanitize free text from users or the LLM**: strip or escape `- | % * @ ( ) { } [ ] " ' : ~`. Otherwise you get odd results or syntax errors.

### Vector index (HNSW)

```cypher
CREATE VECTOR INDEX FOR (c:Chunk) ON (c.embedding)
  OPTIONS {dimension: 1536, similarityFunction: 'cosine', M: 16, efConstruction: 200, efRuntime: 10}
CREATE VECTOR INDEX FOR ()-[e:Call]->() ON (e.summary) OPTIONS {dimension:128, similarityFunction:'euclidean'}
CREATE (:Chunk {text:$t, embedding: vecf32($emb)})              // or SET c.embedding = vecf32($emb)
CALL db.idx.vector.queryNodes('Chunk', 'embedding', $k, vecf32($q)) YIELD node, score
CALL db.idx.vector.queryRelationships('Call', 'summary', $k, vecf32($q)) YIELD relationship, score
DROP VECTOR INDEX FOR (c:Chunk) ON (c.embedding)
```

**Options:**
- `dimension` and `similarityFunction` (`'cosine'` | `'euclidean'`) are **required**. 6.0.2 validates them, and metric names are case-insensitive.
- Dimension range: 1–4096.
- Optional: `M` (16), `efConstruction` (200), `efRuntime` (10).

**Score semantics [SRC]:**
- Score is a **distance**: euclidean is L2, cosine is `1 − cosine_similarity`.
- **Lower means more similar. `ORDER BY score ASC`.** For a similarity, use `1 - score` with cosine.
- The cheat-sheet tip ("cosine, higher is more similar, sort DESC") and the vector-index example (`ORDER BY score DESC`) are DOC-BUGs. Verify once with known vectors.

**Behavior and costs:**
- `vecf32()` elements must be floats.
- A wrong-dimension vector no longer breaks the label's other indexes (fixed in 6.0.2). Still validate dimensions client-side.
- Memory: about `N × dim × 4 bytes × 1.2`. 10k chunks × 1536 dims is roughly 74 MB, which **already exceeds the Cloud free tier (100 MB total)**. Consider 384- or 768-dim embeddings, or self-host.
- **No filtered vector search.** Pattern: over-fetch (`k = 5–10×`), then `WHERE node.team = $team`, then `LIMIT`.
- `k` is clamped in 6.0.2.
- Parameterized `$k` and `vecf32($q)` are used in FalkorDB's own tests. [SRC]

### CCH index (weighted routing): **4.22.x only, not in 6.0.2**

```cypher
CREATE CCH INDEX FOR ()-[e:ROAD|FERRY]->() ON (e.w)
CALL db.idx.cch.query(...)    // signature: see /cypher/indexing/cch-index; not needed for Company Brain
```

### Constraints (GRAPH.CONSTRAINT command, not Cypher)

```bash
GRAPH.QUERY brain "CREATE INDEX FOR (p:Person) ON (p.id)"          # UNIQUE requires a range index first
GRAPH.CONSTRAINT CREATE brain UNIQUE NODE Person PROPERTIES 1 id    # -> PENDING (async)
GRAPH.CONSTRAINT CREATE brain MANDATORY RELATIONSHIP OWNS PROPERTIES 1 since
GRAPH.CONSTRAINT DROP brain UNIQUE NODE Person PROPERTIES 1 id
GRAPH.RO_QUERY brain "CALL db.constraints()"   # YIELD type, label, properties, entitytype, status
```

- Status moves from `UNDER CONSTRUCTION` to `OPERATIONAL`, or to `FAILED` if existing data violates the constraint.
- UNIQUE is enforced only when all its properties are non-null. It is not enforced on array-valued properties.
- An index backing a constraint can't be dropped.
- In Python, `g.create_node_unique_constraint('Person','id')` creates the index for you.

---

## 5. Graph algorithms (exact signatures)

Sources:
- https://docs.falkordb.com/algorithms/ and per-algorithm pages
- https://docs.falkordb.com/cypher/procedures
- Availability checked by grepping registered names in v6.0.2 and v4.22.1

| Procedure | Call signature | YIELD | 6.0.2 (`latest`) | 4.22.1 |
|---|---|---|---|---|
| `algo.pageRank` | `algo.pageRank(label, relType)` (strings or null) | `node, score` (sum = 1.0) | yes | yes |
| `algo.BFS` | `algo.BFS(startNode, maxDepth, relType)`; maxDepth 0 = unlimited | `nodes, edges` | yes | yes |
| `algo.betweenness` | `algo.betweenness({nodeLabels:[..], relationshipTypes:[..], samplingSize:32, samplingSeed:0})` | `node, score` | yes | yes |
| `algo.HarmonicCentrality` | `algo.HarmonicCentrality([{nodeLabels:[..], relationshipTypes:[..]}])` (approximate, HLL); directed | `node, score, reachable` | yes | yes |
| `algo.WCC` | `algo.WCC([{nodeLabels:[..], relationshipTypes:[..]}])` or `algo.WCC(null)` | `node, componentId` | yes | yes |
| `algo.labelPropagation` (CDLP) | `algo.labelPropagation([{nodeLabels, relationshipTypes, maxIterations:10}])` | `node, communityId` | yes | yes |
| `algo.MSF` | `algo.MSF([{nodeLabels, relationshipTypes, weightAttribute:'cost', objective:'minimize'|'maximize'}])`; treats edges as undirected | `edges, nodes` (one row per tree) | yes | yes |
| `algo.SPpaths` | `algo.SPpaths({sourceNode, targetNode, relTypes:[..], weightProp, costProp?, maxCost?, maxLen?, relDirection:'outgoing'|'incoming'|'both', pathCount:1})` (0 = all shortest) | `path, pathWeight, pathCost` | yes | yes |
| `algo.SSpaths` | `algo.SSpaths({sourceNode, relTypes, weightProp?, costProp?, maxCost?, maxLen?, relDirection, pathCount})` | `path, pathWeight, pathCost` | yes | yes |
| `algo.maxFlow` | `algo.maxFlow({sourceNodes:[..], targetNodes:[..], relationshipTypes:[..], capacityProperty:'capacity', nodeLabels?})` | `nodes, edges, edgeFlows, maxFlow` | yes | yes |
| `algo.AStar` | `algo.AStar({sourceNode, targetNode, relTypes, weightProp, latitudeProperty, longitudeProperty, heuristicScale:1.0, relDirection, pathCount})` | `path, pathWeight` | **NO** | yes |
| `db.idx.cch.query` | see CCH page | | **NO** | yes |
| Leiden / Louvain | | | no | no (unreleased) |

**Usage notes:**
- The procedures table lists only some of these. `algo.HarmonicCentrality` and `algo.maxFlow` exist in source but are missing from `procedures.mdx`.
- Names as registered: `algo.WCC` and `algo.BFS` are uppercase, but the WCC and BFS pages also write `algo.wcc(...)` and `algo.bfs(...)`.
  - **Use the registered casing.** Case-insensitive lookup is UNVERIFIED. The 6.0 registry lowercases some built-ins such as `tolower`, which suggests it might be case-insensitive.
- The YIELD clause is optional. Without it, all columns are returned.
- Algorithms run on the live graph. There is no projection or catalog step like Neo4j GDS.
- **Write results back** explicitly to use them in later queries or the UI:
  ```cypher
  CALL algo.pageRank('Person','COLLABORATES_WITH') YIELD node, score SET node.influence = score
  ```
- **Timeouts:** betweenness and labelPropagation on the default Docker image may hit `TIMEOUT 1000` (1 s, read queries). Raise `TIMEOUT_DEFAULT` / `TIMEOUT_MAX`, or pass a per-query timeout.

**Company Brain uses:**
- PageRank or harmonic centrality: "who is central / key expert".
- Betweenness: "bridges between teams, bus-factor people".
- WCC or labelPropagation: "silos / communities".
- SPpaths: "how is X connected to decision Y", with weights such as `1/trust`.
- BFS: "blast radius of a change".

---

## 6. FalkorDB Browser (port 3000)

Sources: https://docs.falkordb.com/browser/ and `/browser/ui/*`

What it does:
- Next.js web UI bundled in `falkordb/falkordb` on :3000. Also available standalone as `falkordb/falkordb-browser`, and in Cloud.
- **Login:** host/port/user/password/TLS with a CA upload, or a URL such as `falkor[s]://user:pass@host:port` (`redis://` and `rediss://` also work).
- **Graph canvas:** force-directed visualization, Style panel, data and graph-info panels, Schema view, Metadata view, Table view, Query editor with history.
- **Graphs Manager:** create, delete, duplicate, export (`.dump` via `/api/graph/{graph}/export`), and upload data. Upload accepts CSV through `LOAD CSV`, or a Cypher batch file (`.cypher` / `.txt`, statements run in sequence).
- **Chat panel:** natural language to Cypher. It needs an LLM key (cloud provider) or a local Ollama (`http://localhost:11434`) or LM Studio (`http://localhost:1234/v1`).
- **Other pages:** UDF Libraries page, built-in Swagger REST docs at `/docs`.
- **Auth:** NextAuth with roles. Read-only users can't create graphs. Admins see DB configuration and user management.

**Demo use:** good for a "look, it's a real graph" moment in the video, e.g. run your provenance query and show the subgraph.
- It is an operator tool, not your product UI. Embed your own visualization in the app, or use the `FalkorDB Canvas` web component, which is documented at `/browser/canvas` and usable standalone.
- **Don't expose an unauthenticated :3000 publicly.**

---

## 7. FalkorDB Cloud (app.falkordb.cloud)

Sources:
- https://docs.falkordb.com/cloud/tiers/free.md
- https://www.falkordb.com/pricing/

| | FREE | STARTUP | PRO | ENTERPRISE |
|---|---|---|---|---|
| Price | $0, no credit card | from **$73/mo** | from $350/mo | custom |
| RAM | **100 MB** (max dataset 100 MB) | from 1 GB (~75% usable for data) | from 8 GB | custom |
| TLS | **✗** | ✓ | ✓ | ✓ |
| Persistence / backups | **none** | backups every 12 h | continuous persistence, backups every 12 h | backups every 2 h |
| HA / multi-zone | ✗ | ✗ | ✓ | ✓ |
| Providers | AWS, GCP | AWS, GCP | AWS, GCP | + Azure |
| Multi-graph, graph access control | ✓ | ✓ | ✓ | ✓ |

**Free-tier terms:** "Free instances that aren't utilized for 1 day will be stopped, and deleted after 7 days. The Free Tier has no persistence and no backups. A deleted instance and its data cannot be recovered."
- Startup: a stopped instance is removed after 14 days, after a snapshot that is kept for 14 days.

**Connection details:**
- The console gives host, port, username and password.
- Connect with `FalkorDB(host=..., port=..., username=..., password=...)` (free tier, no TLS), or `ssl=True` / `falkors://` on paid tiers.
- The exact host format and default username are UNVERIFIED, since we couldn't log in.
- The Cloud engine version (4.x vs 6.0) is UNVERIFIED. Run `CALL dbms.procedures()`.

**Verdict for the hackathon:**
- The free tier is fine for development and as a fallback.
- **It is risky as the judged live deployment.** Judging happens after the deadline, so an idle instance can be stopped within a day. Data is lost on deletion. 100 MB is tight with embeddings, and there is no TLS.
- Better: self-host `falkordb/falkordb-server` with a volume on any VM or PaaS (docs cover Railway and Lightning AI). Or use Startup at $73/mo if Cloud branding matters.
- If you use the free tier anyway, keep a reproducible seed script (`GRAPH.COPY` snapshots don't survive deletion), and ping it with a scheduled query. Whether pings count as "utilized" is UNVERIFIED.

---

## 8. FalkorDB MCP server

Sources:
- https://github.com/FalkorDB/FalkorDB-MCPServer (README, `src/mcp/tools.ts`)
- https://docs.falkordb.com/genai-tools/mcpserver/

**Install:** npm `@falkordb/mcpserver` (1.3.0), or Docker image `falkordb/mcpserver` (`:latest`, `:edge`, `:1.0.0`). Requires Node 18+.

**Tools (exact ids):**

| Tool | Purpose |
|---|---|
| `query_graph` | run Cypher; optional `readOnly` (uses GRAPH.RO_QUERY) and `params` object (`$name`) |
| `query_graph_readonly` | always GRAPH.RO_QUERY |
| `list_graphs` | GRAPH.LIST |
| `delete_graph` | GRAPH.DELETE |
| `get_graph_schema` | labels, rel types, sampled topology `{source:[labels], relationship, target:[labels]}`; `includeConnections`, `connectionSampleSize` (default 10000) |
| `get_node_schema` | sample `sampleSize` (default 100) nodes of a label, rank property keys by frequency |
| `get_relationship_schema` | same for a relationship type |

All schema tools are read-only. Suggested flow: `list_graphs → get_graph_schema → get_node_schema/get_relationship_schema → query_graph`.

**Run it:**
```bash
FALKORDB_HOST=localhost FALKORDB_PORT=6379 npx -y @falkordb/mcpserver                     # stdio
MCP_TRANSPORT=http MCP_PORT=8080 MCP_API_KEY=secret FALKORDB_HOST=... npx -y @falkordb/mcpserver  # Streamable HTTP
docker run -p 8080:8080 -e FALKORDB_HOST=host.docker.internal -e FALKORDB_PORT=6379 \
  -e MCP_TRANSPORT=http -e MCP_PORT=8080 -e MCP_API_KEY=secret falkordb/mcpserver:latest
```

**Environment variables:**
- `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_USERNAME`, `FALKORDB_PASSWORD`.
- `FALKORDB_TLS=true`: uses SNI with the host, so use a DNS name.
- `FALKORDB_DEFAULT_READONLY`.
- `MCP_TRANSPORT=stdio|http`, `MCP_PORT`.
- `MCP_API_KEY`: enforced as `Authorization: Bearer <key>`, in HTTP mode only.
- `ENABLE_FILE_LOGGING`.

**HTTP sessions:**
- POST `initialize`, then send the `Mcp-Session-Id` header.
- Inspect with `npx @modelcontextprotocol/inspector --transport streamable-http --url http://localhost:8080`.

**Caveats:**
- The README claims "FalkorDB does not allow parameters in LIMIT/SKIP". FalkorDB's own test `test_parameterized_skip_limit` passes `SKIP $skip LIMIT $limit`, so the claim is wrong. Variable-length bounds, however, can't be parameters.
- Its bundled compose mounts the volume at `/data`. The README itself says data does not survive `docker compose down` until PR #174 lands.

**Hackathon angle:**
- Plugging the MCP server into Claude Desktop or Cursor gives a "talk to the company brain" demo for free.
- It exposes raw Cypher, though. Your own agent should wrap curated, parameterized query tools: provenance, ownership, impact. That keeps answers source-cited and safe.

---

## 9. Multi-graph, GRAPH.* commands, UDFs, configuration

### Multi-graph / per-tenant

- Each graph is a separate Redis key, chosen with `select_graph('tenant_acme')`.
- Graphs are fully isolated. **Cross-graph queries are not possible**, so keep one graph per demo company.
- Cloud advertises "multi-graph / multi-tenancy" and "graph access control" (ACL; see `/commands/acl`).
- Use `GRAPH.COPY src dst` for cheap snapshots, e.g. "brain as of Monday" versus now, or a reset-to-seed for demos. The source graph stays readable during the copy.
- `GRAPH.LIST` may also show internal keys such as `telemetry{A}` (seen in the docs' example). Filter them out.

### Commands
| Command | Syntax / notes |
|---|---|
| `GRAPH.QUERY` | `GRAPH.QUERY g "CYPHER p1=v1 p2=v2 <query>" [TIMEOUT ms] [--compact] [version v]`. Plans cached (CACHE_SIZE, default 25), so **always parameterize** |
| `GRAPH.RO_QUERY` | same args; errors on any write. Use it for all agent/LLM-generated reads |
| `GRAPH.EXPLAIN g q` | plan without running (check `Index Scan` vs `Label Scan`) |
| `GRAPH.PROFILE g q` | **executes** (writes happen) and returns per-op records/time |
| `GRAPH.LIST` | all graph keys |
| `GRAPH.DELETE g` | drop graph (async by default) |
| `GRAPH.COPY src dst` | clone |
| `GRAPH.SLOWLOG g [RESET]` | top 10 queries ≥ 10 ms |
| `GRAPH.MEMORY USAGE g [SAMPLES n]` | MB breakdown incl. `indices_sz_mb` |
| `GRAPH.INFO [RunningQueries WaitingQueries ObjectPool]` | live queries |
| `GRAPH.CONFIG GET name\|*` / `SET name val [...]` | runtime config (not persisted) |
| `GRAPH.CONSTRAINT CREATE/DROP` | see §4 |
| `GRAPH.UDF LOAD [REPLACE] lib script` / `LIST [lib] [WITHCODE]` / `DELETE lib` / `FLUSH` | JS UDFs |

### JavaScript UDFs (QuickJS)

```python
db.udf_load("Brain", """
function recencyWeight(ts, now) { return Math.exp(-(now - ts) / (1000*60*60*24*30)); }
falkor.register('recencyWeight', recencyWeight);
""", replace=True)
g.query("MATCH (d:Doc) RETURN d.title, Brain.recencyWeight(d.updated_ts, timestamp()) AS w ORDER BY w DESC")
```

What a UDF can access:
- Node objects: `id`, `labels`, `attributes`, and `getNeighbors({direction, types, labels, returnType})`.
- Edge objects: `id`, `type`, `source`, `target`, `attributes`.
- Path objects: `nodes`, `length`, `relationships`.
- The `graph` global: `graph.traverse(...)`, `graph.getNodeById(id)`, `graph.iterateNodes(label)`, `graph.iterateEdges(type)`. `falkor.traverse` was removed in v4.16.
- `falkor.log`.

Limits:
- **UDFs cannot modify the graph.**
- Memory is bounded by `JS_HEAP_SIZE` (256 MB) and `JS_STACK_SIZE` (1 MB).
- **FLEX** library ([github.com/FalkorDB/flex](https://github.com/FalkorDB/flex)) adds ready-made UDFs: `flex.text.levenshtein`/`jaroWinkler` (entity resolution), `flex.sim.jaccard`, `flex.date.*`, `flex.json.*`, `flex.map.*`. It must be loaded as UDF libraries first. The exact namespace at call time is UNVERIFIED.

### Configuration that matters

Each entry gives the default, then whether it can be changed at runtime.

- **`TIMEOUT_MAX` / `TIMEOUT_DEFAULT`** (ms): default 0 (off); runtime-settable.
  - These apply to reads **and writes**. A write that times out is rolled back.
  - The legacy `TIMEOUT` applies to reads only and is deprecated, but **the Docker image sets `TIMEOUT 1000`**.
- **`RESULTSET_SIZE`:** default **10,000**; runtime-settable. Results are truncated silently, so set it higher or paginate.
- **`QUERY_MEM_CAPACITY`:** default unlimited; runtime-settable. Bytes per query.
- **`MAX_QUEUED_QUERIES`:** default unlimited (25 in the Docker image); runtime-settable.
- **`THREAD_COUNT`:** default = logical cores; load-time only.
- **`CACHE_SIZE`:** default 25; load-time only.
- **`IMPORT_FOLDER`:** default `/var/lib/FalkorDB/import/`; load-time only. Base folder for `LOAD CSV file://`.
- **`BOLT_PORT`:** default -1 (off); load-time only.
- **`DELTA_MAX_PENDING_CHANGES`:** default 10,000; runtime-settable.
- **`JS_HEAP_SIZE` / `JS_STACK_SIZE`:** default 256 MB / 1 MB; runtime-settable.

Set them via `FALKORDB_ARGS="TIMEOUT_MAX 30000 TIMEOUT_DEFAULT 15000 RESULTSET_SIZE 50000"` or `GRAPH.CONFIG SET ...`.

---

## 10. Gotchas likely to bite in 30 hours (vs Neo4j habits)

1. **Docs vs engine drift.** The docs describe A*, CCH and `vecf64`, none of which are in `latest`. Several examples are wrong (§0). Treat docs as a hint, and check with `dbms.procedures()` plus a quick test.
2. **Docker defaults:**
   - A 1 s read timeout and a 10k-row cap, both from the image's default `FALKORDB_ARGS`.
   - Persistence only if you mount `/var/lib/falkordb/data` and enable AOF.
   - Pin the image tag.
3. **Parallel-edge counting.** An unreferenced `-[e]->` collapses duplicates. Reference `e` (e.g. `WHERE id(e) >= 0`) when counting messages or mentions.
4. **Eager writes ignore LIMIT.** Do the LIMIT in a `WITH` *before* the write: `MATCH ... WITH x LIMIT 10 SET ...`.
5. **Var-length depth can't be a parameter.** Interpolate a validated int: `f"[:REL*1..{int(depth)}]"`.
6. **No zoned `datetime()`.** Use `localdatetime()` or epoch-ms ints. **Python `datetime` objects can't be params.** Range indexes don't cover temporals, so filter on `*_ts` ints.
7. **No map properties.** Store `meta_json` as a string, or flatten to `meta_source`, `meta_url`.
8. **No null properties.** Setting null removes the key. Use `coalesce()` on read.
9. **No label alternation in node patterns** (`(n:A|B)`). Use `WHERE n:A OR n:B` or a shared super-label such as `:Entity`. Multi-label nodes are supported.
10. **`exists((a)-->(b))` fails.** Use a pattern predicate: `WHERE (a)-->(b)` / `WHERE NOT (a)-->(b)`.
11. **No aggregates inside pattern comprehensions.** Wrap with `size([...])`.
12. **Vector search:**
    - Score is a distance; sort ascending.
    - No pre-filter.
    - Same dimension everywhere.
    - Wrap arrays with `vecf32()` on write **and** query.
    - Index creation requires both `dimension` and `similarityFunction`.
13. **Full-text query strings are RediSearch syntax.** Escape user input. A hyphenated term like `on-call` becomes "on AND NOT call".
14. **MERGE on a whole path duplicates nodes.** MERGE nodes individually, then the edge. Index MERGE keys, and add UNIQUE constraints (async: check `db.constraints()` for `OPERATIONAL`).
15. **`id()` is not stable** after deletes, since ids get reused. Keep your own `id` / `uuid` property (`randomUUID()`) for sources and citations.
16. **No schema introspection query.** Use `db.labels()`, `db.relationshipTypes()`, `db.propertyKeys()`, `db.meta.stats()`, `db.indexes()`, `db.constraints()`. The MCP `get_*_schema` tools do this by sampling.
17. **`GRAPH.PROFILE` executes writes.** Use `EXPLAIN` for a dry run.
18. **APOC does not exist.** Use built-ins, `list.*`, `string.*`, UDFs or FLEX instead. Neo4j-specific syntax not seen in FalkorDB docs is UNVERIFIED: `CALL {} IN TRANSACTIONS`, `apoc.*`, `gds.*`, `CREATE CONSTRAINT ... REQUIRE`, `SHOW INDEXES`, `datetime()`, `elementId()`.
19. **Bulk loading:** use `UNWIND $rows AS r MERGE ...` in batches of roughly 500–5,000 rows. Each `GRAPH.QUERY` is a single atomic write. `falkordb-bulk-loader` (CSV) is available for big imports (`/integration/bulk-loader`).
20. **Cloud free tier sleeps after 1 day idle and is deleted after 7.** Don't make it the judged deployment.

---

## 11. Query templates for a Company Brain (composed from verified syntax; not executed)

```cypher
// Suggested model: (:Person)-[:MEMBER_OF]->(:Team); (:Person)-[:OWNS]->(:Project|:Doc|:Service);
// (:Decision)-[:DECIDED_IN]->(:Meeting|:Thread); (:Decision)-[:SOURCED_FROM {quote, ts}]->(:Message|:Doc|:Ticket);
// (:Decision)-[:SUPERSEDES]->(:Decision); (:Chunk {embedding})-[:PART_OF]->(:Doc|:Message|:Ticket)
// Every node: id (uuid), source_url, created_ts, updated_ts (epoch ms ints); valid_to_ts for superseded facts.

// 1. Hybrid retrieval: vector + graph expansion + citations
CALL db.idx.vector.queryNodes('Chunk', 'embedding', 40, vecf32($q)) YIELD node AS c, score
WITH c, score ORDER BY score ASC LIMIT 12
MATCH (c)-[:PART_OF]->(src)
OPTIONAL MATCH (src)<-[:SOURCED_FROM]-(d:Decision)
OPTIONAL MATCH (owner:Person)-[:OWNS]->(src)
RETURN src.id, labels(src), src.title, src.source_url, 1 - score AS cos_sim,
       collect(DISTINCT d.title) AS decisions, collect(DISTINCT owner.name) AS owners

// 2. Keyword path (sanitize $q first)
CALL db.idx.fulltext.queryNodes('Doc', $q) YIELD node, score
RETURN node.id, node.title, node.source_url, score ORDER BY score DESC LIMIT 10

// 3. Decision provenance: trace to source, including the supersede chain (depth interpolated, not a param)
MATCH (d:Decision {id:$id})
OPTIONAL MATCH chain = (d)-[:SUPERSEDES*1..5]->(old:Decision)
  WHERE NOT (old)-[:SUPERSEDES]->()          // keep only the full chain, not every prefix
OPTIONAL MATCH (d)-[s:SOURCED_FROM]->(src)
RETURN d, [n IN nodes(chain) | n.title] AS history,
       collect({type: labels(src)[0], url: src.source_url, quote: s.quote, ts: s.ts}) AS sources

// 4. Current-only facts (validity window on epoch-ms ints, range-indexable)
MATCH (d:Decision) WHERE d.valid_to_ts IS NULL OR d.valid_to_ts > timestamp()
RETURN d.title ORDER BY d.updated_ts DESC LIMIT 20

// 5. Who knows about X (experts): owners + authors weighted by recency
MATCH (t:Topic {id:$topic})<-[:ABOUT]-(a)<-[r:AUTHORED|OWNS]-(p:Person)
WITH p, count(r) AS n, max(a.updated_ts) AS last
RETURN p.name, n, last ORDER BY n DESC, last DESC LIMIT 5

// 6. Influence / bus factor (write back for UI). TWO separate statements: run each as its own query
CALL algo.pageRank('Person', 'COLLABORATES_WITH') YIELD node, score SET node.influence = score
CALL algo.betweenness({nodeLabels:['Person'], relationshipTypes:['COLLABORATES_WITH'], samplingSeed:42})
  YIELD node, score SET node.bridge = score

// 7. How is person A connected to decision B (undirected shortestPath UNVERIFIED; fallback: algo.SPpaths relDirection:'both')
MATCH (a:Person {id:$a}), (b:Decision {id:$b})
WITH shortestPath((a)-[*..6]-(b)) AS p
RETURN [n IN nodes(p) | coalesce(n.name, n.title)] AS hops, [r IN relationships(p) | type(r)] AS rels

// 8. Keep knowledge current: idempotent upsert from a connector event
UNWIND $rows AS r
MERGE (m:Message {id: r.id})
  ON CREATE SET m.created_ts = r.ts
SET m.text = r.text, m.updated_ts = timestamp(), m.source_url = r.url
WITH m, r
MERGE (p:Person {id: r.author_id})
MERGE (p)-[:AUTHORED]->(m)
```

---

## Source index

- **Docs repo:** https://github.com/FalkorDB/docs (commit f804fd7, 2026-10-15). Site: https://docs.falkordb.com/
- **Engine:** https://github.com/FalkorDB/FalkorDB (tags v6.0.2, v4.22.1; releases page)
- **Python client:** https://github.com/FalkorDB/falkordb-py, https://pypi.org/project/falkordb/
- **Node client:** https://github.com/FalkorDB/falkordb-ts, https://www.npmjs.com/package/falkordb
- **MCP:** https://github.com/FalkorDB/FalkorDB-MCPServer, https://www.npmjs.com/package/@falkordb/mcpserver
- **Cloud:** https://docs.falkordb.com/cloud/tiers/free, https://www.falkordb.com/pricing/
- **Raw downloads** (untrusted, for re-checking):
  - `C:\Users\petst\AppData\Local\Temp\claude\C--Info-Hackathons-Graph-Hacks-2026\e98f7fa8-13eb-43ff-982d-44b9276a5e21\scratchpad\falkor-docs\`
  - `C:\Users\petst\AppData\Local\Temp\claude\C--Info-Hackathons-Graph-Hacks-2026\e98f7fa8-13eb-43ff-982d-44b9276a5e21\scratchpad\falkor-src\`
