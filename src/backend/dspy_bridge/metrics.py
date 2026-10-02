"""Metrics for DSPy optimisation.

Two families:

  objective  the Cypher generator. Scored by executing the query and comparing
             the returned paths to a gold set derived from live graph state. No
             LLM judge, so it is cheap, deterministic and reproducible.

  judged     synthesis and verification. Scored by an LLM judge plus a
             groundedness check on citations.

Objective metrics matter disproportionately: a judge is itself an LLM call, so
judged metrics cost money on every optimisation trial and are noisier. Anything
that *can* be scored by execution, should be.
"""
from __future__ import annotations

import re
from collections.abc import Callable

# ── path normalisation ──────────────────────────────────────────────────────
_PUNCT = re.compile(r"[^\w/.\-]+")


def normalise_path(p: str) -> str:
    """Make path comparison forgiving of prefixes and separators.

    Questions say "repo_parser.py" where the graph stores
    "src/backend/chunking/repo_parser.py", and separators vary. Comparing
    basenames plus a normalised tail avoids punishing a correct answer for
    spelling out a directory the question did not mention.
    """
    s = _PUNCT.sub("", str(p or "").strip().lower())
    return s.replace("\\", "/")


def path_f1(predicted: list[str], gold: list[str]) -> tuple[float, float, float]:
    """Set F1 over paths, matching on exact-or-basename.

    Returns (f1, precision, recall).
    """
    g = {normalise_path(x) for x in gold if x}
    p = {normalise_path(x) for x in predicted if x}
    if not g and not p:
        return 1.0, 1.0, 1.0
    if not g or not p:
        return 0.0, 0.0, 0.0

    def matches(pred: str, gold: str) -> bool:
        if pred == gold:
            return True
        # A bare filename matches whichever gold path it names.
        return "/" not in pred and pred in gold.rsplit("/", 1)[-1]

    used: set[str] = set()
    tp = 0
    for pred in p:
        for i, gold in enumerate(g):
            if i in used:
                continue
            if matches(pred, gold):
                tp += 1
                used.add(i)
                break

    precision = tp / len(p)
    recall = tp / len(g)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return f1, precision, recall


# ── citation / groundedness ─────────────────────────────────────────────────
_PATH_RE = re.compile(r"[\w\-./]+\.(?:py|js|jsx|ts|tsx|json|yaml|yml|toml|md|txt)")


def cited_files(text: str) -> list[str]:
    return [normalise_path(m) for m in _PATH_RE.findall(text or "")]


def citation_precision(answer: str, context: str) -> float:
    """Fraction of cited paths that actually appear in the retrieved context.

    Catches invented filenames, which are the most common way a synthesiser
    sounds confident while being wrong.
    """
    cited = cited_files(answer)
    if not cited:
        return 0.0
    pool = {normalise_path(c) for c in _PATH_RE.findall(context or "")}
    hits = 0
    for c in cited:
        if c in pool or ("/" not in c and any(c in p.rsplit("/", 1)[-1] for p in pool)):
            hits += 1
    return hits / len(cited)


def answer_mentions_gold_paths(answer: str, gold_paths: list[str]) -> float:
    """Fraction of gold paths the answer names.

    A recall-style check on the behavioural examples, where there is no exact
    gold string but there are files the answer should mention.
    """
    if not gold_paths:
        return 1.0
    mentioned = set(cited_files(answer))
    hits = 0
    for g in gold_paths:
        gn = normalise_path(g)
        if gn in mentioned or ("/" not in gn and any(
            gn == m.rsplit("/", 1)[-1] for m in mentioned
        )):
            hits += 1
    return hits / len(gold_paths)


# ── DSPy metric factories ───────────────────────────────────────────────────
def structural_metric(executor: Callable[[str, str], list[str]]):
    """Metric for Cypher generation.

    `executor(cypher, repo_id) -> list[path]` runs the query and returns the
    paths it produced. An empty result is a score of 0, not a skip: a query that
    returns nothing is a failed query for these examples.
    """

    def metric(example, prediction, trace=None) -> float:
        cypher = getattr(prediction, "cypher", None)
        if not cypher:
            return 0.0
        try:
            paths = executor(cypher, example.repo_id)
        except Exception:
            return 0.0
        f1, _, _ = path_f1(paths, example.expected_paths)
        return f1

    return metric


def verification_metric(judge: Callable[[str, str, str], str] | None = None):
    """Metric for the verifier: penalise it for approving bad answers.

    A verifier that says "supported" to everything is useless, so the metric
    rewards agreeing with the judge and rewards flagging genuine problems.
    """

    def metric(example, prediction, trace=None) -> float:
        verdict = (getattr(prediction, "verdict", "") or "").strip().lower()
        if verdict not in ("supported", "unsupported", "off_topic"):
            return 0.0
        if judge is None:
            return 1.0
        truth = judge(example.question, example.context, example.draft)
        predicted_ok = verdict == "supported"
        actual_ok = "supported" in truth.lower()
        return 1.0 if predicted_ok == actual_ok else 0.0

    return metric


def synthesis_metric(judge: Callable[[str, str, str], float] | None = None):
    """Metric for synthesis: judge score, gated on citation grounding.

    The gate stops a fluent answer from scoring well when it invents filenames,
    which is the failure mode that matters most here.
    """

    def metric(example, prediction, trace=None) -> float:
        answer = getattr(prediction, "answer", "") or ""
        context = example.get("context", "") if isinstance(example, dict) else getattr(example, "context", "")
        grounding = citation_precision(answer, context)
        if grounding == 0.0:
            return 0.0
        score = judge(example.question, context, answer) if judge else 1.0
        return 0.5 * score + 0.5 * grounding

    return metric
