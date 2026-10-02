# Ask My Repo

Natural-language Q&A over any GitHub repository. Point it at a repo and it builds a
dependency graph (Neo4j) plus a vector index (Qdrant), so you can ask structural and
behavioural questions in plain English.

```
┌──────────────────────────────────────────────────────────┐
│                      API (FastAPI)                       │
│  POST /api/parse   POST /api/chat   GET /api/graph_data  │
└────┬────────────────────┬────────────────────────────────┘
     │                    │
     ▼                    ▼
┌──────────┐      ┌──────────────┐
│ Indexing │      │    Query     │
│ Pipeline │      │  Pipeline    │
│ (async)  │      │  (LangGraph) │
└──────────┘      └──────────────┘
     │                    │
     └────────┬───────────┘
              ▼
     ┌──────────────────┐
     │  Neo4j + Qdrant  │
     └──────────────────┘
```

---

## Query Pipeline

`src/backend/chat_engine/engine.py:ChatWorkflow` — a LangGraph `StateGraph`. Each node
reads and writes `AgentState`.

```
Router ──architecture──► Architect ──► Synthesizer ──► Verify ──► END
  │
  ├──graph_only──► Graph ──meaningful?──► Synthesizer
  │                    │
  │                    └──no data──► Vector
  │
  └──hybrid──► QueryRewriter ──► Graph ∥ Vector ──► Synthesizer
                                 │       │
                                 └── merge ┘
```

`∥` means the vector search starts concurrently with Cypher generation — see
[Speculative retrieval](#speculative-retrieval).

### Router

Classifies into three paths:

| Route | For |
|---|---|
| `graph_only` | imports, dependencies, definitions, call graphs — answerable from structure alone |
| `architecture` | request flows, entry-point call chains, dependency maps, class interaction |
| `hybrid` | everything else: behaviour, logic, variable values, feature implementation |

Decisions are cached by query text in `_router_cache`, so a repeated question costs one
fewer model call. The DSPy route, when enabled, writes to the same cache.

### QueryRewriter

Resolves pronouns and references (`it`, `that`, `the function`) using the last 8 turns of
history.

**The gate.** A model call is skipped when the question cannot need a rewrite — no history,
or no back-reference, or it names its own target. A dangling pronoun like *"What does it
return?"* still goes to the model.

Targets are matched by *internal* signal (second capital, underscore, digit, file
extension). A plain `[A-Z]\w+` would match the first word of any sentence, so *"What does it
return?"* would look self-contained and the gate would never fire — which is exactly what
happened before it was fixed. Set `ASK_NO_GATES=1` to always rewrite.

### Architect

System-level queries via predefined Cypher templates:

| Template | Description |
|---|---|
| `entry_call_chain` | Entry point → downstream call chains (up to 6 hops) |
| `request_flow` | Entry point → sink functions, depth-sorted |
| `dependency_map` | File → transitive import chains (up to 4 hops) |
| `class_interaction` | Cross-class method calls and instantiations |
| `system_overview` | Per-file function, class, entry point inventory |

Subtype comes from keyword matching, with an LLM fallback on a miss. Results are enriched
with vector search over critical-path files (up to 4 files, top-4 chunks each).

Vector enrichment has its own `try` block. It used to share one with the graph search, so a
single vector failure discarded graph records that had already been fetched successfully.

### Graph

1. **Template matching** — regex over common question shapes. Zero model calls.

   | Template | Matches |
   |---|---|
   | `direct_imports` | which files import X |
   | `reverse_lookup` | what imports X |
   | `transitive_dependencies` | what X depends on, transitively |
   | `multi_hop_imports` | X → Y → Z import chains |
   | `leaf_nodes` | files with no imports |
   | `most_dependencies` | the file importing the most others |
   | `call_graph` | who calls a symbol |
   | `class_hierarchy` | class inheritance chains |
   | `file_structure` / `all_files` | inventory queries |

2. **Model-generated Cypher** — if no template matches.

Both paths converge on `_execute_generated_cypher()`, which is where all safety handling
lives and must stay shared:

```
sanitize_cypher  →  resolve_names_in_cypher  →  parameterize_literals  →  READ transaction
```

Writes are rejected twice over: `sanitize_cypher` matches against a denylist, and
`_run_read` opens the session with `default_access_mode="READ"` so the driver refuses a
write even if the sanitizer missed it. `parameterize_literals` then lifts every remaining
literal into a parameter, so the executed text is fully under our control.

All templates are parameterised. They previously used `str.format()`, which made the
repo_id — derived from a user-supplied URL by `get_filename()` — an injection vector:
`get_filename("https://github.com/a')-DETACH DELETE n//b")` produced `a')-DETACH DELETE n-`.
The architect templates were already parameterised; the graph ones were not.

Results are cached in `_graph_cache` keyed by query string.

### Vector

Searches Qdrant for chunks matching the query, filtered to files extracted from graph
results when available.

> **On `rerank()`** — when Qdrant returns pre-computed scores, `rerank()` re-sorts by
> exactly the score it was given. It is currently a no-op with extra steps, and a real
> reranker (bge-reranker / Cohere) is not wired in. This is a known gap, not a feature.

### Synthesizer

Formats graph and vector results into `[Graph Relationships]` + `[Code Chunks]` and asks the
model for an answer with a confidence score and source attribution.

### Verify

A cheap-model groundedness gate. Runs **only** where it can change the outcome:

```
confidence < 0.7  OR  context < 800 chars
```

Verifying a confident, well-grounded answer would double cost to re-confirm something that
was already fine.

A negative verdict is **not** acted on by itself. It is only surfaced when an objective
check agrees — the fraction of the answer's cited file paths that actually appear in the
retrieved context. That measurement needs no model, so a cheap model's misjudgement cannot
override it. The threshold is strict (0.8): grounding is a precision measure over cited
paths, so an answer naming three real files and one invented file is still inventing.

The verifier never rewrites prose. On a negative verdict it appends the gap and zeroes the
confidence. **Context includes the question** — naming the file you were asked about is not
hallucinating it.

### Speculative retrieval

`src/backend/chat_engine/speculate.py` starts the unfiltered vector search on a worker
thread while Cypher generation runs. Measured against a 2s Cypher call:

| | |
|---|---|
| vector search alone | 293 ms |
| sequential (`2s` + search) | 2532 ms |
| overlapped | 2000 ms |

Both database clients are synchronous and neither is documented as thread-safe, so
`ASK_NO_SPECULATE=1` forces everything inline.

Only the **hybrid** path speculates. A meaningful graph result goes straight to the
synthesizer, so speculating there would start a search, pay for it, and discard it.

> **Methodology note.** End-to-end p50 varied 4.95s–7.57s for an *identical* configuration
> across runs — larger than any effect worth detecting. So the latency figures above come
> from isolating the segment, not from end-to-end timing. Any future timing work should clear
> `_graph_cache` and `_router_cache` between configurations and run A B B A, or the second
> configuration will read a cache the first one populated and appear to save model calls it
> cannot possibly save.

### State

`AgentState` (`src/backend/agent_state/state.py`) holds `user_query`, `rewritten_query`,
`router_decision`, `reason`, `graph_result`, `vector_result`, `context`, `final_answer`,
`user_history`, `current_agent`. History is persisted server-side per session (last 16
turns) in `session_histories`.

---

## DSPy Integration

`src/backend/dspy_bridge/` replaces the individual prompts with DSPy programs. LangGraph
still orchestrates — DSPy replaces prompts, never the graph topology.

| Program | Module | Replaces | Default model |
|---|---|---|---|
| `route` | `ChainOfThought` | router prompt | cheap |
| `rewrite` | `Predict` | rewriter prompt | cheap |
| `cypher` | `Predict` | Cypher generation prompt | cheap |
| `synthesize` | `ChainOfThought` | synthesizer prompt | strong |
| `verify` | `Predict` | *(new — groundedness gate)* | cheap |

`GenerateCypher` uses `Predict` rather than chain-of-thought deliberately: the output is
syntax-constrained, so free-text reasoning before it adds tokens and failure modes. It reads
the graph schema by introspecting the live Neo4j graph, falling back to a hardcoded copy.

**Off by default.** `ASK_DSPY=1` opts in. Every program returns `None` on any failure and the
caller falls back to the original prompt, so enabling it cannot produce a 500.

```
ASK_DSPY=1                     serve answers from DSPy programs
DSPY_TIER=mixed|cheap|strong   which model serves which program
DSPY_CHEAP / DSPY_STRONG       override the two model names
ASK_DSPY_SHADOW=1              run DSPy, discard the result, log agreement
ASK_NO_GATES=1                 always run the rewriter (measures the gate)
ASK_NO_SPECULATE=1             never overlap retrieval with Cypher generation
DSPY_OPT_TIER                  model assignment used while optimising
```

Tier assignments are complete by construction — every tier maps every program. An earlier
partial mapping fell through to a default for four of five programs and silently served them
all from the synthesize model, which is why identical scores appeared across supposedly
different tiers. `tests/test_dspy_bridge.py::TestModelBinding` asserts that `mixed` actually
differs from a uniform assignment.

`bind_models()` must be called after `build_programs()`. It walks to the *leaf* predictor,
because `ChainOfThought` is not a `Predict` subclass and binding the wrapper sets an
attribute nothing reads.

### Shadow mode

Runs the DSPy programs on every request, discards the results, and records token-overlap
agreement against the legacy path. Use it to gather evidence before switching a program over.

It is **off by default** and does not require `ASK_DSPY=1`: a shadow run makes a real model
call per request whose output is thrown away, so defaulting it on would bill every request
for nothing.

### Optimising

```bash
# regenerate the eval set from live graph state (labels are derived, not hand-written)
uv run python -m src.evaluation.build_eval_set

# score the current prompts
uv run python -m src.evaluation.optimize --program cypher --mode eval

# compare tiers on the same split
uv run python -m src.evaluation.optimize --program cypher --mode eval --tier strong

# optimise, then replay against the held-out split
uv run python -m src.evaluation.optimize --program cypher --mode optimize
uv run python -m src.evaluation.optimize --program cypher --mode replay --split test
```

Cypher is scored by **executing the query** and comparing returned paths to a gold set
derived from live graph state — an objective metric needing no LLM judge, which makes it the
cheapest and least noisy optimisation target. Generated queries go through the same sanitizer
as the live path, so a query that could not execute at runtime scores zero during
optimisation too.

Compiled artefacts land in `src/evaluation/saved/` (gitignored). Without one, programs run on
their hand-written instructions. `MIPROv2` has not been run yet — `--mode replay --split
test` is the only number that would show whether optimisation generalised.

See `docs/dspy-design.md` for measurements and an explicit list of what is and is not yet
established.

---

## Indexing Pipeline

Triggered by `POST /api/parse`, orchestrated in `src/backend/map/mapper.py:map_repository()`.
Runs asynchronously with progress reporting.

### Stage 1 — Fetch

`repo_parser.py:clone_repo()` clones into `src/data/<repo_id>`. Only `.py` files and
`README.md` are read.

`_refresh_clone` refuses to `git reset --hard` a directory that is not a self-contained
clone. Without that check, pointing the tool at a path inside an existing repository walks
up to that repository and destroys its uncommitted work. `tests/test_clone_safety.py`
reproduces the incident.

### Stage 2 — Parse (AST)

`repo_parser.py:parse_file()` extracts, per file: imports; classes (name, qualified name,
bases, line ranges); functions and methods (name, qualified name, parent class, line
ranges); the call graph (BFS, no nested descent); entry points (HTTP decorators, `__main__`,
CLI and task decorators, app-runner calls); and flagged candidates — conventional entry
filenames needing LLM review.

### Stage 3 — Graph Building

`chunk_builder.py:ChunkBuilder.build()` builds a name index (symbol → file) and a module
index (Python path → file), resolves imports against them into a NetworkX directed graph,
then `push_to_neo4j()` writes it out:

```
Repo ──CONTAINS──► File
File ──IMPORTS──► File
File ──IMPORTS_SYMBOL──► Import
File ──DEFINES_CLASS──► Class
File ──DEFINES_FUNCTION──► Function
Class ──HAS_METHOD──► Function
Class ──INHERITS_FROM──► Class
Class ──INHERITS_EXTERNAL──► ExternalSymbol
Function ──CALLS──► Function
Function ──INSTANTIATES──► Class
Function ──CALLS_EXTERNAL──► ExternalSymbol
```

File-level dependencies are the `File ──IMPORTS──► File` edge. `Import` nodes record symbols
a file imported, *including third-party ones*, and carry no information about which local
file imported what. Getting this backwards is what dropped the Cypher metric to F1 = 0.000
until the schema was corrected.

AST-detected entry points are annotated on Function/File nodes.

### Stage 4 — Entry Point LLM Review

Files flagged in Stage 2 are sent to an LLM with their function names, decorators, and
imports. It classifies each as `http_endpoint`, `main_block`, `app_runner`, `cli_entry`,
`task_entry`, or not an entry point. Results merge back via `apply_entry_points()`.

### Stage 5 — Vector Indexing

`vector_db.py:VectorStore.build()` chunks by class boundaries (overlap 5 lines), function and
method boundaries including decorators, and module-level segments. Capped at 100 lines /
12,000 characters, each tagged with path, class, function, and line range.

`VectorStore.push()` batches to Qdrant (`EMBED_BATCH_SIZE`, default 4) with dense
`jinaai/jina-embeddings-v2-base-code` and sparse `Qdrant/bm25` embeddings. UUIDs are derived
deterministically from path + function + line range.

### Stage 6 — Visualization

`ChunkBuilder.show()` generates an interactive PyVis graph saved to `notebook/`.

### Duplicate detection

Before indexing, the pipeline checks Qdrant and Neo4j for existing data
(`VectorStore.collection_exists()`, `ChunkBuilder.repo_exists()`). If both exist it reuses
them. Partial rebuilds are handled too. `map_repository` raises rather than reporting success
when a repo yields zero files.

### Progress

| Stage | Progress | Description |
|---|---|---|
| `fetching` | 8% | Clone repository |
| `graph_building` | 22% | AST parse + NetworkX build |
| `graph_saving` | 42-45% | Push to Neo4j + LLM entry review |
| `vector_building` | 55% | Chunk + embed |
| `vector_saving` | 58-90% | Push to Qdrant |
| `assistant_ready` | 95% | Finalising |

---

## LLM Fallback

`llm_fallback.py:FallbackChatModel` wraps `ChatOpenAI` with failover to Groq's
`llama-3.3-70b-versatile`. On `openai.OpenAIError` it retries via Groq with the same messages,
implements `with_structured_output` for Pydantic parsing, and tracks state via `_fallback_used`.

## Cleanup

`repo_activity.py:RepoActivityTracker` runs a daemon thread that records activity per repo
and, after `REPO_INACTIVITY_TIMEOUT_HOURS` (default 3) of inactivity, wipes session caches →
deletes the Neo4j graph → deletes the Qdrant collection → removes the visualisation. Failed
cleanups retry with exponential backoff. Checked every `CLEANUP_CHECK_INTERVAL_MINUTES`
(default 5). Manual trigger: `POST /api/cleanup/manual/{repo_id}`.

## Evaluation

Two separate things, easy to confuse:

**`src/evaluation/eval.py`** — RAGAS scoring over `questions.json` (20 hand-written
questions). *Currently broken*: the resolved `ragas` release imports
`langchain_community.chat_models.vertexai`, which no longer exists. The import is lazy and
`pyproject.toml` notes the pin problem, so the rest of the module still imports and the
`__main__` guard works — but the scoring step will not run until the dependency is resolved.

**`src/evaluation/build_eval_set.py`** — generates a second eval set whose labels are
**derived from live Neo4j state**, not hand-written, so they stay correct when the graph is
rebuilt. 34 examples across 3 repos, 25 of them objective (gold = an exact path set).

---

## Project Structure

```
src/
├── backend/
│   ├── api.py                     FastAPI server, lifespan, CORS allow-list
│   ├── job_status.py              Async job tracking
│   ├── agent_state/state.py       LangGraph AgentState
│   ├── chat_engine/
│   │   ├── engine.py              LangGraph query workflow
│   │   ├── gates.py               Rewriter skip heuristic
│   │   └── speculate.py           Overlapped vector retrieval
│   ├── chunking/
│   │   ├── repo_parser.py         Git clone + AST analysis
│   │   └── chunk_builder.py       NetworkX graph + Neo4j push
│   ├── map/mapper.py              Indexing pipeline orchestrator
│   ├── dspy_bridge/
│   │   ├── signatures.py          Five dspy.Signature classes
│   │   ├── programs.py            dspy.Module wrappers + live schema introspection
│   │   ├── config.py              Model tiers, bind_models()
│   │   ├── metrics.py             Path F1, citation grounding
│   │   └── runtime.py             The only thing engine.py touches
│   └── services/
│       ├── vector_db.py           Qdrant chunking/embedding/search
│       ├── query_engine.py        Templates + generated Cypher, sanitizer
│       ├── connections.py         Shared Neo4j driver
│       ├── llm_fallback.py        OpenAI → Groq fallback
│       ├── cache.py               TTL+LRU cache (currently unused — see below)
│       └── repo_activity.py       Inactivity cleanup daemon
├── evaluation/
│   ├── eval.py                    RAGAS evaluation (broken — see above)
│   ├── build_eval_set.py          Derives eval labels from live graph state
│   ├── optimize.py                DSPy optimisation entry point
│   ├── questions.json             20 hand-written questions
│   └── eval_set.json              Generated, derived labels
├── data/                          Cloned repos (gitignored)
├── models/                        Local model cache (gitignored)
└── frontend/                      React UI (Vite + Tailwind)
```

`src/backend/services/cache.py` implements a TTL+LRU cache with repo-scoped invalidation, but
**nothing calls it yet** — the graph and router caches in use are separate, query-string-keyed
dicts on `QueryEngine`.

---

## Configuration

All via `.env` (see `.env.example`):

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | LLM for Q&A and query generation |
| `GROQ_API_KEY` | No | LLM fallback |
| `NEO4J_URI` | Yes | Neo4j connection string |
| `NEO4J_USER` | Yes | Neo4j username |
| `NEO4J_PASS` | Yes | Neo4j password |
| `QDRANT_END_POINT` | Yes | Qdrant cluster URL |
| `QDRANT_API_KEY` | Yes | Qdrant API key |
| `CORS_ORIGINS` | No | Allowed origins, comma-separated (default `http://localhost:5173,http://127.0.0.1:5173`) |
| `EMBED_BATCH_SIZE` | No | Chunks per Qdrant push batch (default 4) |
| `REPO_INACTIVITY_TIMEOUT_HOURS` | No | Cleanup timeout (default 3) |
| `CLEANUP_CHECK_INTERVAL_MINUTES` | No | Cleanup interval (default 5) |

CORS is an explicit allow-list, not `["*"]` with credentials — the combination is rejected by
browsers and is not a valid wildcard configuration.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Neo4j and Qdrant reachability |
| `POST` | `/api/parse` | Start indexing (async) |
| `GET` | `/api/parse/status/{job_id}` | Indexing progress |
| `POST` | `/api/chat` | Ask a question |
| `GET` | `/api/graph/{repo_id}` | PyVis HTML visualisation |
| `GET` | `/api/tree/{repo_id}` | File paths |
| `GET` | `/api/graph_data/{repo_id}` | Nodes and edges for the React Flow view |
| `GET` | `/api/activity` | Per-repo activity timestamps |
| `POST` | `/api/cleanup/manual/{repo_id}` | Force cleanup |

`POST /api/chat` takes `repo_url`, `query`, `session_id`. Opening a session no longer clones
the repository: the file inventory reaches `VectorStore.build()`, which only runs during
indexing, so it is behind a lazy `Mapping` rather than parsed on the request path.

---

## Quick Start

```bash
uv sync --group dev
uv run uvicorn src.backend.api:app --reload

cd src/frontend
npm install
npm run dev
```

Open `http://localhost:5173`, enter a GitHub repo URL, and start asking questions.

Check `GET /health` first when a parse fails.

---

## Development

```bash
uv run pytest                        # 250 tests, no database or network required
uv run ruff check src tests app.py
cd src/frontend && npm run lint
```

The suite never opens a socket: credentials are stubbed in `tests/conftest.py` and units are
constructed without their live-client wiring. CI runs tests, ruff, and the frontend lint +
build on every push.

### Known gaps

| Gap | Impact |
|---|---|
| `rerank()` re-sorts by the score it was given | No actual reranking; cross-encoder not wired |
| `eval.py` RAGAS scoring | Cannot run — `ragas` imports a module removed from `langchain_community` |
| `cache.py` unwired | No response caching in production |
| No compiled DSPy artifacts | Programs run on hand-written instructions |
| Cheap-vs-strong tier gap unproven | 0.042 F1 on 16 examples — not enough to justify either direction |
| End-to-end latency unmeasured | Provider variance exceeds every effect measured |

### Frontend notes

`ReactFlowGraph.jsx` computes a layout via BFS shortest-path rather than depth-based
placement (which diverges on cycles), and wires `onNodeClick` to `<ReactFlow>` — it was
being passed to a component that returns `null`. `NodeDetails.jsx` runs its hooks before the
early return and filters properties through an allow-list.