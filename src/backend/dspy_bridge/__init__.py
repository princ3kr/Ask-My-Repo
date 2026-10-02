"""DSPy bridge: signatures, modules and metrics for the query pipeline.

LangGraph stays the orchestrator. Each node's prompt becomes a DSPy module, so
optimization improves the prompt without changing the graph topology.

Module tiers, chosen for cost:
  RouteQuestion    cheap  classification, ~10 output tokens
  RewriteQuestion  cheap  short extraction
  GenerateCypher   cheap  has an *objective* metric — no judge required
  VerifyAnswer     cheap  a check, not a generation
  SynthesizeAnswer strong  the only step that genuinely needs the big model

Nothing here imports LangGraph or the databases, so the modules are testable in
isolation and can be compiled offline.
"""
