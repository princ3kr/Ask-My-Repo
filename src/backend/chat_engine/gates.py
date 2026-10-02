"""Gates that skip work before it costs an LLM call.

Each gate is a pure function so it can be tested without a model, and each one
only returns True when skipping is provably safe. A false negative costs one
wasted call; a false positive returns a wrong answer, so the gates lean toward
running the call.

`ASK_NO_GATES=1` disables all of them, which is how the cost of the gates
themselves gets measured against the same questions.
"""
from __future__ import annotations

import os
import re

# Words that refer back to something already said. Their presence is what
# makes a follow-up question unanswerable on its own.
BACK_REFERENCES = re.compile(
    r"\b(it|its|it's|that|this|these|those|they|them|their|the same|"
    r"the above|the previous|previous|earlier|above|also|and then|"
    r"one|ones|the other|the first|the second|there)\b",
    re.IGNORECASE,
)

# A question that names a concrete target is self-contained regardless of
# history, so back-references in it are being used loosely rather than as
# anaphora.
#
# The identifier patterns deliberately require an *internal* signal — a second
# capital, a underscore, a digit, or an extension. A plain `[A-Z]\w+` would
# match the first word of any sentence ("What does it return?" -> "What") and
# silently disable the gate for most follow-ups.
_PATHY = r"\w+\.(?:py|js|jsx|ts|tsx|json|md|toml|ya?ml|txt|cfg|ini|html|css)\b"
_CAMEL = r"\b[A-Z][a-z]+(?:[A-Z][A-Za-z0-9]*)+\b"      # QueryEngine, ChatWorkflow
_SNAKE = r"\b[a-z]+(?:_[a-z0-9]+)+\b"                  # vector_search, graph_search
_ACRONYM = r"\b[A-Z]{2,}[A-Z0-9_]*\b"                  # IMPORTS, Neo4j, API
_UNDERSCORE_CLASS = r"\b[A-Z][a-z]+(?:_[A-Za-z0-9]+)+\b"  # Graph_Result, BaseModel

CONCRETE_TARGET = re.compile(
    f"({_PATHY}|{_CAMEL}|{_SNAKE}|{_ACRONYM}|{_UNDERSCORE_CLASS}|"
    r"`[^`]+`|\"[^\"]+\")"
)


def gates_enabled() -> bool:
    return os.getenv("ASK_NO_GATES", "0") != "1"


def needs_rewrite(question: str, history: list | None) -> bool:
    """Whether the rewriter's LLM call is worth making.

    Two cases are provably safe to skip:

    - **No history.** There is nothing to resolve a reference against, so the
      rewrite would be the identity function.
    - **No back-reference.** The question already stands alone. A pronoun like
      "it" in a question that names `query_engine.py` is not pointing at
      anything missing.

    The concrete-target check is what keeps the second rule from firing on
    "What does it return?" — that has a back-reference and no named target,
    so it still goes to the model.
    """
    if not gates_enabled():
        return True
    if not history:
        return False
    if not BACK_REFERENCES.search(question or ""):
        return False
    if CONCRETE_TARGET.search(question or ""):
        # Named target present. Only skip when the reference is not the thing
        # being asked about — approximated by the question starting with a
        # demonstrative rather than naming the target first.
        stripped = (question or "").strip()
        if not re.match(r"^(it|its|they|them|that|this|these|those)\b", stripped, re.I):
            return False
    return True


def graph_result_is_usable(graph_res, is_meaningful) -> bool:
    """Whether the graph result alone is enough to skip vector retrieval.

    Thin wrapper so the routing decision lives in one tested place instead of
    being re-derived at each call site.
    """
    if graph_res is None:
        return False
    try:
        return bool(is_meaningful(graph_res))
    except Exception:
        return False
