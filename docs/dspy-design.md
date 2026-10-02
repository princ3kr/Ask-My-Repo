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

It runs only where it can change the outcome — a self-reported confidence below
0.7, or a context under 800 characters. Verifying a confident, well-grounded
answer would double cost to re-confirm something that was already fine. It
also never rewrites prose: it reports whether the answer is supported, and a
negative verdict appends the gap and zeroes the confidence. A reader is better
served by an honest "not in the retrieved context" than by a confident invention
with the same retrieval behind it.

### Model tiers

Three complete assignments, selectable with `DSPY_TIER`:

| Tier | route / rewrite / cypher / verify | synthesize |
|---|---|---|
| `strong` | gpt-4o | gpt-4o | baseline to measure against |
| `mixed` | **gpt-4o-mini** | gpt-4o | default |
| `cheap` | gpt-4o-mini | gpt-4o-mini | experiment: what does the small model cost in quality? |

Every tier defines every program. An earlier version had `TIER_STRONG` mapping
only `synthesize`, so `model_for` fell through to a default for the other four
and the cheap model ended up serving synthesis by accident — the opposite of the
intent, and invisible because it still worked.

### Why parallel

The vector search does not need the graph result: it filters by filenames, but
an unfiltered search is a valid first pass. Today they are sequential
(`graph → vector`), so the embedding round-trip is pure added latency.

Only the hybrid path speculates. A meaningful graph result goes straight to the
synthesizer, so speculating there would start a search, pay for it, and discard
it. Both backends are synchronous blocking clients, so a thread is the only
thing that actually overlaps them.

`ASK_NO_SPECULATE=1` forces everything inline. Neither client is documented as
thread-safe, so that is the switch to reach if a deployment ever sees
cross-request interference.

## Measured, not projected

Latency and cost were the stated goals, so they were measured rather than
argued for. Two methodology notes, because the first attempt got both wrong.

**Measuring call counts with a warm cache is meaningless.** `QueryEngine`
memoises by query string, so whichever configuration ran second read a cache the
first one had populated and skipped the LLM call entirely. Speculation appeared
to cut calls from 16 to 12, which it cannot do — it moves no work off any LLM
call. Every timed block now clears both caches first.

**Run order is not a control.** Run A B B A, so ordering bias cancels in the
difference.

| Change | Calls | Tokens | Latency |
|---|---|---|---|
| Rewriter gate (6 follow-up questions) | 21 → 19 | ~26.9k → ~26.9k | within noise |
| Speculative retrieval | unchanged | unchanged | 2532ms → 2000ms on that segment |

**The rewriter gate saves calls, not time.** It removes an LLM call from
follow-up questions that are already self-contained — 2 of the 6 in the suite —
and saves nothing measurable in wall clock, because provider latency variance
between runs exceeded the effect. Its value is API spend, and it is real: the
eliminated call is the most expensive one in the pipeline, since the rewriter
prompt carries the conversation history.

**Speculation is the latency win, and it is measured directly.** End-to-end
p50 for the *same* configuration varied between 4.95s and 7.57s across runs —
larger than any effect worth detecting, so end-to-end timing at this sample size
cannot support a claim either way. Isolating the segment removes provider
variance: against a 2s Cypher generation, the vector search costs 293ms
sequential and 0ms overlapped.

So: **293ms per hybrid question, no change to call count, no change to answer
quality.** Worth having, and worth being precise about, because it is a fifth
of a typical hybrid request rather than the two-thirds the call-count framing
suggested.

**Honest status of the cost claim.** The tier assignment is not yet a measured
saving. What has been measured is that the cheap tier produces valid Cypher
(1.1–2.3s per query, returning correct rows for all three probe questions) and
that the current prompts score F1 = 0.532 on the eval set at that tier. What has
*not* been done is the comparison that would justify the switch: evaluate the
same split at `mixed` and `strong`, optimise, and replay on the held-out split.
Until that runs, "the cheap model is good enough" is an assumption.

### Why the cheap-model tier is viable

Three of the five modules are classification/extraction (route, rewrite,
verify) where a small model with an optimised prompt matches a large one. The
Cypher generator has an *objective* metric, which is the ideal optimiser target
— DSPy can iterate against "does this return the right rows" without a judge.
Only synthesis genuinely needs the strong model.

DSPy's compiled artefact is a set of prompts + few-shot demos, so the same
program serves both tiers: compiled against the strong model, then *replayed*
on the cheap model to measure what the degradation actually costs.

## What the eval set found

The eval set was built to enable optimisation. It immediately found two bugs
that no amount of prompt tuning would have fixed.

**The hand-written schema was wrong.** First evaluation run: F1 = 0.000. The
schema described `Class` and `Function` with properties they do not have, and
never mentioned that `(File)-[:IMPORTS]->(File)` is the edge that answers
dependency questions. The model reached for `Import` nodes instead — which
record symbols a file imported, including third-party ones, and carry no
information about which local file imported what — and returned nothing. The
schema is now introspected from the deployed graph. F1 = 0.532, with the
cheapest tier.

**The rewriter gate never fired.** Its target regex matched any
sentence-initial capital, so "What does it return?" counted as self-contained
and the gate skipped every question that started with a capitalised word.
Targets now require an internal signal: a second capital, an underscore, a
digit, or a file extension.

Both are the kind of bug that reads as a quality problem and is actually a
data problem, which is the argument for building the eval set first.

## Rollout

1. `dspy_bridge` — signatures + modules, wrapping existing call sites. **done**
2. Eval set — 3 repos, objective where possible. **done** (34 examples, 25 objective)
3. Gates + speculation — latency work that stands alone. **done**
4. Shadow mode — run both, log agreement, change nothing.
5. Optimise — `MIPROv2` on the Cypher metric first (objective), then `GEPA`
   for the judge-based modules.
6. Cut over per module, keeping a rollback flag.

Each stage is independently useful; if optimisation is abandoned, stages 1–3
still cut latency.

DSPy is **off by default** (`ASK_DSPY=1` opts in). Every entry point returns
`None` on any failure and the caller uses the existing LangChain prompt, so a
DSPy problem degrades to current behaviour rather than a 500.

## Constraints carried over from the existing code

- `sanitize_cypher` stays the security boundary. DSPy prompting the model more
  cleverly must not widen it. The objective metric runs generated queries
  *through the sanitizer* so a query that could not execute at runtime scores
  zero during optimisation too — otherwise DSPy optimises toward a query that
  is rejected on arrival.
- Every read still runs in a READ transaction.
- Compiled prompts must not leak between repos.
- The verifier is a quality gate, not a security gate. It is LLM-judged.

## Commands

```bash
# regenerate the eval set from live graph state
uv run python -m src.evaluation.build_eval_set

# baseline: current prompts, cheapest tier
uv run python -m src.evaluation.optimize --program cypher --mode eval

# what does the strong model cost more than the cheap one?
uv run python -m src.evaluation.optimize --program cypher --mode eval --tier strong

# optimise, then replay the artefact against the held-out split
uv run python -m src.evaluation.optimize --program cypher --mode optimize
uv run python -m src.evaluation.optimize --program cypher --mode replay --split test
```
