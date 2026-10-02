"""DSPy programs, one per pipeline node.

Each wraps a signature with an explicit reasoning step where it earns its keep
and a direct prediction where it does not. `GenerateCypher` is deliberately
`dspy.Predict` rather than `ChainOfThought`: the query is a syntax-constrained
output, and reasoning in free text before it only adds tokens and failure
modes.

`build_programs()` returns all five so an optimiser can compile them together.
"""
from __future__ import annotations

import dspy

from src.backend.dspy_bridge.signatures import (
    CypherSignature,
    RewriteSignature,
    RouteSignature,
    SynthesizeSignature,
    VerifySignature,
)


# Read from live graph state. A hand-written schema drifts: it once described
# Class and Function with properties they do not have, and omitted that
# File-IMPORTS-File is the edge that actually answers dependency questions —
# which is why the model reached for Import nodes instead.
def build_graph_schema() -> str:
    """Introspect the Neo4j graph and describe labels, properties and edges."""
    try:
        from src.backend.services.connections import get_neo4j_driver

        with get_neo4j_driver().session(default_access_mode="READ") as s:
            labels: dict[str, set[str]] = {}
            for r in s.run(
                "MATCH (n) WHERE n.repo_id IS NOT NULL "
                "WITH labels(n)[0] AS lab, n UNWIND keys(n) AS k "
                "RETURN DISTINCT lab, k"
            ):
                labels.setdefault(r["lab"], set()).add(r["k"])

            edges: set[str] = set()
            for r in s.run(
                "MATCH (a)-[r]->(b) WHERE a.repo_id IS NOT NULL AND b.repo_id IS NOT NULL "
                "RETURN DISTINCT labels(a)[0] AS a, type(r) AS t, labels(b)[0] AS b"
            ):
                edges.add(f"({r['a']})-[:{r['t']}]->({r['b']})")
    except Exception:
        return FALLBACK_SCHEMA

    lines = ["Nodes:"]
    for lab in sorted(labels):
        lines.append(f"  {lab}({', '.join(sorted(labels[lab]))})")
    lines.append("")
    lines.append("Relationships:")
    lines.extend(f"  {e}" for e in sorted(edges))
    return "\n".join(lines)


# Used when the database is unreachable, e.g. when compiling offline.
FALLBACK_SCHEMA = """
Nodes:
  Repo(repo_id, fingerprint)
  File(path, name, repo_id, is_entry, imports, functions, classes)
  Class(qualified_name, name, path, repo_id, bases, line_start, line_end)
  Function(qualified_name, name, path, class_name, repo_id, line_start, line_end, is_entry, entry_kind, entry_confidence)
  Import(path, name, module, alias, line, repo_id)
  ExternalSymbol(name, repo_id)

Relationships:
  (Repo)-[:CONTAINS]->(File)
  (File)-[:IMPORTS]->(File)
  (File)-[:IMPORTS_SYMBOL]->(Import)
  (File)-[:DEFINES_CLASS]->(Class)
  (File)-[:DEFINES_FUNCTION]->(Function)
  (Class)-[:HAS_METHOD]->(Function)
  (Class)-[:INHERITS_FROM]->(Class)
  (Class)-[:INHERITS_EXTERNAL]->(ExternalSymbol)
  (Function)-[:CALLS]->(Function)
  (Function)-[:INSTANTIATES]->(Class)
  (Function)-[:CALLS_EXTERNAL]->(ExternalSymbol)

Key point: file-level dependencies are the (File)-[:IMPORTS]->(File) edge, not
the Import nodes. Import nodes record symbols a file imported, including
third-party ones; the IMPORTS edge only links local files.
"""

CYPHER_SCHEMA = FALLBACK_SCHEMA

class RouteQuestion(dspy.Module):
    """Classify a question into a retrieval path."""

    def __init__(self):
        super().__init__()
        self.classify = dspy.ChainOfThought(RouteSignature)

    def forward(self, question: str):
        return self.classify(question=question)


class RewriteQuestion(dspy.Module):
    """Resolve references in a follow-up question."""

    def __init__(self):
        super().__init__()
        self.rewrite = dspy.Predict(RewriteSignature)

    def forward(self, question: str, history: str = ""):
        return self.rewrite(history=history, question=question)


class GenerateCypher(dspy.Module):
    """Write a read-only Cypher query for a structural question.

    `graph_schema` defaults rather than being required: DSPy calls `forward`
    with only the keys the example declares as inputs, so a required third
    parameter silently scores zero on every example.
    """

    def __init__(self, graph_schema: str | None = None):
        super().__init__()
        # Introspected at construction so the prompt always matches the graph
        # actually deployed, not a description that drifted from it.
        self.graph_schema = graph_schema or build_graph_schema()
        self.generate = dspy.Predict(CypherSignature)

    def forward(self, question: str, repo_id: str, graph_schema: str | None = None):
        return self.generate(
            question=question,
            repo_id=repo_id,
            graph_schema=graph_schema or self.graph_schema,
        )


class SynthesizeAnswer(dspy.Module):
    """Produce the final answer from retrieved context."""

    def __init__(self):
        super().__init__()
        self.synthesize = dspy.ChainOfThought(SynthesizeSignature)

    def forward(self, question: str, context: str, history: str = ""):
        return self.synthesize(question=question, context=context, history=history)


class VerifyAnswer(dspy.Module):
    """Check a draft answer against the context it claims to come from."""

    def __init__(self):
        super().__init__()
        self.verify = dspy.Predict(VerifySignature)

    def forward(self, question: str, context: str, draft: str):
        return self.verify(question=question, context=context, draft=draft)


def build_programs() -> dict[str, dspy.Module]:
    return {
        "route": RouteQuestion(),
        "rewrite": RewriteQuestion(),
        "cypher": GenerateCypher(),
        "synthesize": SynthesizeAnswer(),
        "verify": VerifyAnswer(),
    }
