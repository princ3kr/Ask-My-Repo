"""DSPy language-model configuration, with an explicit cost tier per program.

Latency and cost are both dominated by *which* model serves a step, so the tier
is a property of the program rather than a global setting. `DSPY_TIER` swaps
the whole assignment at once, which is how the cheap-model experiment is run
without touching call sites.

Two independent knobs:
  DSPY_TIER      cheap | strong   which assignment to use
  DSPY_STRONG    override the strong model
  DSPY_CHEAP     override the cheap model
"""
from __future__ import annotations

import logging
import os

import dspy

logger = logging.getLogger("askmyrepo.dspy")

STRONG_MODEL = os.getenv("DSPY_STRONG", "openai/gpt-4o")
CHEAP_MODEL = os.getenv("DSPY_CHEAP", "openai/gpt-4o-mini")

PROGRAMS = ("route", "rewrite", "cypher", "synthesize", "verify")

# Three named assignments, all of which define every program. A partial mapping
# would make `model_for` fall through to a default that nobody chose, which is
# how the cheap model ended up serving synthesis by accident.
#
#   strong — everything on the large model. The baseline to measure against.
#   mixed  — the shipping default. Synthesis keeps the large model because it
#            is the one step whose output quality the user actually reads;
#            routing, rewriting, Cypher and verification move to the small one.
#   cheap  — everything on the small model. The experiment that answers
#            "how much does the small model cost us in quality?"
TIERS: dict[str, dict[str, str]] = {
    "strong": dict.fromkeys(PROGRAMS, STRONG_MODEL),
    "mixed": {**dict.fromkeys(
        ("route", "rewrite", "cypher", "verify"), CHEAP_MODEL
    ), "synthesize": STRONG_MODEL},
    "cheap": dict.fromkeys(PROGRAMS, CHEAP_MODEL),
}

# An unknown tier is a config mistake, and falling through silently would run
# work on a model nobody selected. Treat it as "mixed", the default.
DEFAULT_TIER = "mixed"


def tier_for(name: str | None = None) -> str:
    """Resolve a tier name to a known tier.

    Validates here rather than only in `assignment_for` so that a caller
    reading the tier (logging, reporting, `DspyRuntime.tier`) never sees a
    value that is not a real tier. An unrecognised name falls back to
    `DEFAULT_TIER` and logs, because silently serving traffic on a model
    nobody chose is worse than falling back to the documented default.
    """
    tier = (name or os.getenv("DSPY_TIER", DEFAULT_TIER)).strip().lower()
    if tier not in TIERS:
        logger.warning(
            "Unknown DSPy tier %r; using %r. Valid tiers: %s",
            tier, DEFAULT_TIER, ", ".join(sorted(TIERS)),
        )
        return DEFAULT_TIER
    return tier

_configured: dict[str, dspy.LM] = {}


def assignment_for(tier: str | None = None) -> dict[str, str]:
    """The full program -> model mapping for a tier."""
    return TIERS[tier_for(tier)]


def configure_lms(tier: str | None = None) -> dict[str, dspy.LM]:
    """Build (and cache) one LM per distinct model name, and set the default.

    One `dspy.LM` per model, not per program: two programs on the same model
    share a client, which matters because DSPy's disk cache and HTTP
    connection pool both live on it.
    """
    assignment = assignment_for(tier)
    lms = {name: _lm_for(name) for name in sorted(set(assignment.values()))}
    # The default is the model synthesis uses, so an un-annotated call lands on
    # the tier-appropriate one.
    dspy.configure(lm=lms[assignment["synthesize"]], adapter=dspy.ChatAdapter())

    logger.info(
        "DSPy configured (tier=%s): %s",
        tier_for(tier),
        ", ".join(f"{k}={v}" for k, v in sorted(assignment.items())),
    )
    return lms


def _leaf_predictors(program) -> list[dspy.Predict]:
    """Every `Predict` a module will actually call.

    `RouteQuestion.classify` is a `ChainOfThought`, whose `predict` attribute
    is the `Predict` that issues the request. Binding the wrapper would set an
    attribute nothing reads.
    """
    found: list[dspy.Predict] = []

    def visit(obj, depth: int = 0) -> None:
        # The isinstance test has to come first: a leaf `Predict` has no
        # `predict` attribute of its own, so probing for it first would
        # discard exactly the object being looked for.
        if depth > 3 or obj is None:
            return
        if isinstance(obj, dspy.Predict):
            found.append(obj)
            return
        inner = getattr(obj, "predict", None)
        if inner is not None:
            visit(inner, depth + 1)

    for attr in dir(program):
        if attr.startswith("_"):
            continue
        try:
            value = getattr(program, attr)
        except Exception:
            continue
        if isinstance(value, dspy.Predict):
            found.append(value)
        elif isinstance(value, dspy.Module):
            visit(value)
    return found


def bind_models(programs: dict, tier: str | None = None) -> dict:
    """Attach each program's tier model to the predictors that call it.

    Without this the tier tables do nothing. A `dspy.Predict` resolves
    `dspy.settings.lm` at call time when it carries no `lm` of its own, so
    every program silently runs on whichever model is configured as the global
    default — which is the *synthesize* model. The cost of a cheap routing tier
    would then be zero in the configuration and the full price on every
    request.

    `lm` is set on the leaf predictor. A `dspy.Predict` reads `self.lm`, and a
    `ChainOfThought` delegates to an inner `Predict` (`cot.predict`), so
    binding has to reach through the wrapper. Checking only for a `Predict`
    attribute would leave the ChainOfThought programs unbound -- which is
    exactly what happened, silently, until a test asked which model a call
    would actually reach.

    Programs with no reachable predictor are left alone and reported, so an
    unexpected shape surfaces here rather than running on the wrong model.
    """
    assignment = assignment_for(tier)
    lms = {name: _lm_for(name) for name in sorted(set(assignment.values()))}
    unbound = []

    for name, program in programs.items():
        predictors = _leaf_predictors(program)
        if not predictors:
            unbound.append(name)
            continue
        for predictor in predictors:
            predictor.lm = lms[assignment[name]]

    if unbound:
        logger.warning(
            "no dspy.Predict found in %s; they will run on the global LM "
            "(%s). Check the program shape.",
            ", ".join(sorted(unbound)), assignment["synthesize"],
        )
    logger.debug(
        "bound %d of %d programs to %s",
        len(programs) - len(unbound), len(programs), tier_for(tier),
    )
    return lms


def _lm_for(name: str) -> dspy.LM:
    if name not in _configured:
        # cache=True is what makes an optimisation run affordable: the
        # optimiser re-issues identical prefixes constantly.
        _configured[name] = dspy.LM(name, temperature=0.0, max_tokens=1024, cache=True)
    return _configured[name]


def model_for(program: str, tier: str | None = None) -> str:
    """Which model a given program should use."""
    return assignment_for(tier).get(program, STRONG_MODEL)
