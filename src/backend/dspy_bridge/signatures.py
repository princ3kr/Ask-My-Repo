"""DSPy signatures — one per LLM step in the pipeline.

Field descriptions are instructions to the model, so they are written as
constraints rather than prose. DSPy's optimiser rewrites these, which is why
they state *what good looks like* rather than spelling out the procedure.
"""
import dspy


# ── 1. routing ──────────────────────────────────────────────────────────────
class RouteSignature(dspy.Signature):
    """Decide which retrieval path answers a question about a code repository.

    The knowledge graph holds structure only: files, imports, classes,
    functions, methods, inheritance, call edges, entry points. It has no
    runtime state and no variable values.

    Choose graph_only when the answer is a set of files or a relationship
    readable straight from that structure. Choose architecture when the
    question is about how the whole system connects or how work flows through
    it. Choose hybrid for everything else — anything about what code does,
    what a value is, or how a feature is implemented.

    When torn between graph_only and hybrid, choose hybrid.
    """

    question: str = dspy.InputField()
    route: str = dspy.OutputField(
        desc="Exactly one of: graph_only, architecture, hybrid."
    )
    reason: str = dspy.OutputField(
        desc="One sentence on why this path fits the question."
    )


# ── 2. query rewriting ──────────────────────────────────────────────────────
class RewriteSignature(dspy.Signature):
    """Make a follow-up question answerable on its own.

    Replace pronouns and references ("it", "that function", "the file above")
    with the concrete names they refer to, taken from the conversation. Do not
    add information the conversation does not contain, and do not change the
    question's intent.

    If the question is already self-contained, return it unchanged.
    """

    history: str = dspy.InputField(
        desc="Recent conversation, oldest first."
    )
    question: str = dspy.InputField()
    standalone_question: str = dspy.OutputField(
        desc="The question with every reference resolved to a concrete name."
    )


# ── 3. Cypher generation ────────────────────────────────────────────────────
class CypherSignature(dspy.Signature):
    """Write one read-only Neo4j query answering a structural question.

    Schema:
      Repo(repo_id)
      File(path, name, functions, classes, imports, repo_id)
      Class(qualified_name, name, path, bases, line_start, line_end, repo_id)
      Function(qualified_name, name, path, class_name, line_start, line_end, repo_id)
      Import(name, module, alias, path, line, repo_id)
      ExternalSymbol(name, repo_id)

    Relationships:
      (Repo)-[:CONTAINS]->(File)
      (File)-[:IMPORTS]->(File)
      (File)-[:DEFINES_CLASS]->(Class)
      (File)-[:DEFINES_FUNCTION]->(Function)
      (Class)-[:HAS_METHOD]->(Function)
      (Class)-[:INHERITS_FROM]->(Class)
      (Class)-[:INHERITS_EXTERNAL]->(ExternalSymbol)
      (Function)-[:CALLS]->(Function)
      (Function)-[:INSTANTIATES]->(Class)
      (Function)-[:CALLS_EXTERNAL]->(ExternalSymbol)

    Rules:
      1. Always scope with MATCH (r:Repo {repo_id: $repo_id}) and pass repo_id
         as a parameter. Never inline a repo id as a literal.
      2. Read-only. Never emit CREATE, MERGE, SET, DELETE, REMOVE, DROP or CALL.
      3. Return file paths as strings, not nodes — the caller formats them.
      4. Use DISTINCT when returning many rows.
      5. File-level dependencies are the (File)-[:IMPORTS]->(File) edge. To find
         which files import a given file, match the *target* of that edge. Use
         `b.path ENDS WITH 'name.py'` so a bare filename matches regardless of
         directory depth. Do NOT route this through Import nodes: those record
         symbols a file imported, including third-party ones, and carry no
         information about which local file imported what.
      6. For transitive or multi-hop, use a variable-length pattern such as
         -[:IMPORTS*1..3]->.
      7. Use no property outside the schema above.
    """

    repo_id: str = dspy.InputField(desc="Repository scope for the query.")
    graph_schema: str = dspy.InputField(desc="Node labels, properties and relationships.")
    question: str = dspy.InputField()
    cypher: str = dspy.OutputField(
        desc="A single read-only Cypher statement. Parameters are $named."
    )


# ── 4. synthesis ────────────────────────────────────────────────────────────
class SynthesizeSignature(dspy.Signature):
    """Answer a question about a code repository from retrieved context only.

    The context holds two sections: [Graph Relationships], giving structural
    facts such as which files import which, and [Code Chunks], giving verbatim
    source with file paths and line numbers.

    Cite the file path inline for every claim. If the context does not answer
    the question, say what it does cover rather than speculating.

    Prefer code that constructs or initialises a value when asked about a
    default or initial value.
    """

    context: str = dspy.InputField(
        desc="Retrieved graph relationships and code chunks."
    )
    history: str = dspy.InputField(desc="Recent conversation, may be empty.")
    question: str = dspy.InputField()
    answer: str = dspy.OutputField(
        desc="The answer, with file paths cited inline."
    )
    confidence: float = dspy.OutputField(
        desc="0.0-1.0. How well the context actually supports the answer."
    )
    used_files: str = dspy.OutputField(
        desc="Comma-separated paths the answer relies on."
    )


# ── 5. verification ─────────────────────────────────────────────────────────
class VerifySignature(dspy.Signature):
    """Judge whether a draft answer is actually supported by the retrieved context.

    Be strict. A claim the context does not support is unsupported even if it is
    plausible, and even if it sounds like something this codebase would do.

    supported means every substantive claim traces to the context. A partial
    answer that is honest about its gaps is better supported than a confident
    one that invents.
    """

    question: str = dspy.InputField()
    context: str = dspy.InputField()
    draft: str = dspy.InputField()
    verdict: str = dspy.OutputField(
        desc="Exactly one of: supported, unsupported, off_topic."
    )
    worst_gap: str = dspy.OutputField(
        desc="The single most unsupported claim, or 'none'."
    )
