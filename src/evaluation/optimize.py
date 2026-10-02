"""Optimise the DSPy programs against the eval set.

    # measure the current prompts (baseline)
    uv run python -m src.evaluation.optimize --program cypher --mode eval

    # optimise, then save the artefact
    uv run python -m src.evaluation.optimize --program cypher --mode optimize

    # replay a saved artefact against the held-out test split
    uv run python -m src.evaluation.optimize --program cypher --mode replay

    # what does the cheap model cost us?
    uv run python -m src.evaluation.optimize --program cypher --mode eval --tier strong

Cypher optimisation is the default and the most interesting target: its metric
is objective (execute the query, compare returned paths to a gold set derived
from live graph state), so MIPROv2 optimises against ground truth rather than a
judge. That makes it both the cheapest optimisation to run and the least noisy.

Judge-based programs fall back to citation grounding, which needs no judge
either. An LLM judge is only used when DSPY_JUDGE=1, because a judge costs an
LLM call per trial and the objective metrics are better if they can be used.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-7s | %(name)-22s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("askmyrepo.optimize")
for noisy in ("httpx", "httpcore", "litellm", "neo4j", "qdrant_client", "openai"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import dspy  # noqa: E402

from src.backend.dspy_bridge.config import (  # noqa: E402
    STRONG_MODEL,
    TIERS,
    configure_lms,
    model_for,
)
from src.backend.dspy_bridge.metrics import (  # noqa: E402
    structural_metric,
)
from src.backend.dspy_bridge.programs import (  # noqa: E402
    build_programs,
)

EVAL_SET = ROOT / "src" / "evaluation" / "eval_set.json"
SAVED_DIR = ROOT / "src" / "evaluation" / "saved"


# ── eval set loading ────────────────────────────────────────────────────────
def load_split(split: str) -> list[dspy.Example]:
    data = json.loads(EVAL_SET.read_text())
    rows = data[split]
    examples = []
    for r in rows:
        ex = dspy.Example(
            question=r["question"],
            repo_id=r["repo_id"],
            expected_paths=r["expected_paths"],
            expected_answer=r["expected_answer"],
            kind=r["kind"],
            qid=r["qid"],
        ).with_inputs("question", "repo_id")
        examples.append(ex)
    return examples


# ── executor for the objective metric ───────────────────────────────────────
def _lm(name: str):
    """One configured LM for a model name, independent of tier assignment."""
    return configure_lms()[name]


def execute_cypher(cypher: str, repo_id: str) -> list[str]:
    """Run a generated query and pull file paths out of the result.

    Goes through the same sanitizer as the live path. A query that would be
    rejected at runtime must also score zero during optimisation, otherwise
    DSPy will happily optimise toward a query that cannot execute.
    """
    from src.backend.services.query_engine import QueryEngine

    engine = QueryEngine.__new__(QueryEngine)
    safe = engine.sanitize_cypher(cypher)
    if not safe:
        return []
    safe, params = engine.parameterize_literals(safe)
    params["repo_id"] = repo_id

    from src.backend.services.connections import get_neo4j_driver

    try:
        with get_neo4j_driver().session(default_access_mode="READ") as s:
            rows = [r.data() for r in s.run(safe, **params)]
    except Exception as e:
        logger.debug(f"generated query failed: {type(e).__name__}: {e}")
        return []

    paths: list[str] = []
    for row in rows:
        for v in row.values():
            if isinstance(v, str) and "/" in v and "." in v.rsplit("/", 1)[-1]:
                paths.append(v)
            elif isinstance(v, list):
                paths.extend(
                    x for x in v
                    if isinstance(x, str) and "/" in x and "." in x.rsplit("/", 1)[-1]
                )
    return paths


def cypher_examples(examples: list[dspy.Example]) -> list[dspy.Example]:
    """Only the examples whose gold label is a path set."""
    return [e for e in examples if getattr(e, "expected_paths", None)]


# ── modes ───────────────────────────────────────────────────────────────────
def run_eval(program_name: str, split: str, tier: str) -> float:
    configure_lms(tier)
    programs = build_programs()
    program = programs[program_name]

    examples = cypher_examples(load_split(split))
    if not examples:
        logger.error("no objective examples in split %r", split)
        return 0.0

    metric = structural_metric(execute_cypher)
    evaluator = dspy.Evaluate(
        devset=examples, metric=metric, num_threads=4,
        display_progress=False, display_table=False, max_errors=20,
    )
    # dspy.Evaluate returns an EvaluationResult whose `.score` is 0-100, while
    # the metric itself is 0-1. Normalise so two runs are comparable.
    result = evaluator(program)
    score = float(getattr(result, "score", result))
    if score > 1.0:
        score /= 100.0
    n = len(examples)
    print(f"\n{program_name} [{tier}] {split}: F1 = {score:.3f}  (n={n})")
    return score


def run_optimize(program_name: str, split: str, tier: str, auto: str) -> None:
    configure_lms(tier)
    programs = build_programs()
    program = programs[program_name]

    examples = cypher_examples(load_split(split))
    if len(examples) < 4:
        logger.error("need >=4 examples to optimise on, have %d", len(examples))
        return

    metric = structural_metric(execute_cypher)

    # The strong model proposes instruction candidates; the program is scored
    # on the tier's own model. Proposing with the strong model is worth the
    # tokens because candidate *quality* is what the search is really
    # selecting on, and it costs far fewer calls than the trials would.
    prompt_lm = _lm(STRONG_MODEL)

    optimizer = dspy.MIPROv2(
        metric=metric,
        prompt_model=prompt_lm,
        task_model=_lm(model_for(program_name, tier)),
        auto=auto,
        max_bootstrapped_demos=4,
        max_labeled_demos=4,
        num_threads=4,
        verbose=True,
        log_dir=str(SAVED_DIR / program_name),
    )

    logger.info(
        "optimising %r on %d examples (auto=%s, task model=%s)",
        program_name, len(examples), auto, model_for(program_name, tier),
    )
    optimizer.compile(program, trainset=examples, eval_kwargs={"num_threads": 4})

    SAVED_DIR.mkdir(parents=True, exist_ok=True)
    out = SAVED_DIR / f"{program_name}.json"
    program.save(out)
    logger.info("saved -> %s", out)


def run_replay(program_name: str, split: str, tier: str) -> float:
    """Score a saved artifact against the held-out split.

    This is the only number that matters: `optimize` reports the best score it
    found *on the training split*, which is optimistically biased by
    construction. Replaying on `test` is what says whether any of it
    generalised.
    """
    configure_lms(tier)
    path = SAVED_DIR / f"{program_name}.json"
    if not path.exists():
        logger.error("no saved programme at %s", path)
        return 0.0
    program = build_programs()[program_name]
    program.load(path)
    return run_eval(program_name, split, tier)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--program", default="cypher",
                    choices=["route", "rewrite", "cypher", "synthesize", "verify"])
    ap.add_argument("--mode", default="eval", choices=["eval", "optimize", "replay"])
    ap.add_argument("--split", default="train", choices=["train", "test"])
    ap.add_argument("--tier", default=None,
                    choices=sorted(TIERS),
                    help="Model assignment to evaluate. Default: $DSPY_OPT_TIER, "
                         "else 'cheap'. Optimising on the cheap tier is "
                         "deliberate: the artefact is prompts and demos, which "
                         "are portable across models.")
    ap.add_argument("--auto", default="light", choices=["light", "medium", "heavy"])
    args = ap.parse_args()

    # Optimizing on the cheap model keeps the search cheap; the artefact is
    # prompts and demos, which are portable across models. So default to the
    # cheapest tier regardless of what serves traffic.
    tier = args.tier or os.getenv("DSPY_OPT_TIER", "cheap")

    if not EVAL_SET.exists():
        logger.error("no eval set at %s — run build_eval_set.py first", EVAL_SET)
        return 1

    if args.mode == "eval":
        run_eval(args.program, args.split, tier)
    elif args.mode == "optimize":
        run_optimize(args.program, args.split, tier, args.auto)
    else:
        run_replay(args.program, args.split, tier)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
