# DSPy integration — design

## Goals, in priority order

1. **Latency** — p50 under ~4s, no path with more than 2 sequential LLM calls.
2. **API cost** — minimise calls, and make the cheap model good enough that it
   can carry the routine work.
3. **Answer quality** — never regress against the current prompts.

## Current cost/latency profile (measured)

| Path | LLM calls | Sequential? |
|---|---|---|
| `graph_only` + template hit | 1 | yes |
| `graph_only` + Cypher gen | 3 | yes |
| `architecture` + template hit | 2 | yes |
| `architecture` + LLM subtype | 3 | yes |
| `hybrid`, no history | 2 | yes |
| `hybrid`, with history | 4 | yes |

Worst case 4 sequential round-trips. Measured from a real run: router ~3.0s,
Cypher gen ~2.5s, vector ~6.0s cold (includes ONNX download), synth ~3.0s —
**~22s cold, ~14.5s warm**.

## Architecture

```
                    ┌─────────────── PARALLEL ───────────────┐
                    │                                        │
              router (DSPy)                          vector search
              cheap model                            (no LLM call)
                    │                                        │
                    └─── merge ──────────────────────────────┘
                              │
              graph only ──────┴──── hybrid/architecture
                    │                       │
              template match?          template match?
              yes: done (0 calls)      yes: done
              no:  CypherGen (DSPy,     no:  rewriter (DSPy, cheap)
                   parallel w/ synth      then CypherGen only if needed
                              │
                     verify (DSPy, cheap)  ← the quality gate
                              │
                    pass → answer    fail → re-retrieve once, then answer
```

### The five DSPy modules

| Module | Replaces | Model tier | Metric |
|---|---|---|---|
| `RouteQuestion` | `engine.router_node` | cheap | agreement with human labels |
| `RewriteQuestion` | `engine.query_rewriter_node` | cheap | rewrite contains resolved referent |
| `GenerateCypher` | `query_engine` Cypher prompt | cheap | **objective**: runs, returns gold rows |
| `SynthesizeAnswer` | `AnswerEngine` | strong | LLM judge + groundedness |
| `VerifyAnswer` | *new* | cheap | grounded / supported / relevant |

`VerifyAnswer` is the one that buys quality per token: a cheap model checks the
answer against the retrieved context, so a bad retrieval is caught before the
user sees a confident wrong answer, and a strong model is only spent when the
cheap path was uncertain.

### Why parallel

The router and the vector search are independent — the vector search does not
need to know the route. Today they are sequential (`router → graph → vector`),
so the embedding query round-trip is pure added latency. Firing them together
removes one full network round-trip from every hybrid question.

Graph and synthesis stay dependent on routing, because generating Cypher before
knowing the question is structural would waste calls on most questions.

### Why the cheap-model tier is viable

Three of the five modules are classification/extraction (route, rewrite,
verify) where a small model with an optimised prompt matches a large one. The
Cypher generator has an *objective* metric, which is the ideal optimiser target
— DSPy can iterate against "does this return the right rows" without a judge.
Only synthesis genuinely needs the strong model.

DSPy's compiled artefact is a set of prompts + few-shot demos, so the same
program serves both tiers: compiled against the strong model, then *replayed*
on the cheap model to measure what the degradation actually costs.

## Rollout

1. `dspy_bridge` — signatures + modules, wrapping existing call sites.
2. Eval set — 3 repos, objective where possible.
3. Shadow mode — run both, log agreement, change nothing.
4. Optimise — `MIPROv2` on the Cypher metric first (objective), then `GEPA`
   for the judge-based modules.
5. Cut over per module, keeping a rollback flag.

Each stage is independently useful; if optimisation is abandoned, stages 1–3
still cut latency via parallelism.

## Constraints carried over from the existing code

- `sanitize_cypher` stays the security boundary. DSPy prompting the model more
  cleverly must not widen it.
- Every read still runs in a READ transaction.
- `cache.py` invalidation on re-index still applies; compiled prompts must not
  leak between repos.
