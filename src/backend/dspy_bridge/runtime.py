"""Runtime wrapper the pipeline calls, so engine.py never imports DSPy directly.

Design rules, in order of importance:

1. **Never break the pipeline.** Every method returns `None` on any failure, and
   the caller falls back to the existing LangChain prompt. A DSPy problem
   degrades to today's behaviour rather than to a 500.

2. **Never cost more than the fallback.** The cheap tier is the default. A tier
   that turns out to be worse is a config change, not a code change.

3. **Measure before switching.** `shadow=True` runs the DSPy program, scores it
   against the same gold the optimizer uses, logs the agreement, and returns
   `None` so the legacy result is what reaches the user. That makes the
   migration reversible at every step.

The compiled artefact is optional. Without one, the programs run on their
hand-written instructions, which is a valid (if unoptimised) starting point.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger("askmyrepo.dspy")

SAVED_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "saved"


def _truthy(value: str | None, default: bool = False) -> bool:
    """Env-var boolean.

    os.getenv returns a string, and "0" is truthy in Python -- so a plain
    `if os.getenv(...)` treats "0" as enabled. That is the exact bug that made
    ASK_DSPY_SHADOW untrustworthy: setting it to 0 could not turn it off.
    """
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _similar(a: str, b: str) -> bool:
    """Token-overlap agreement between two answers.

    Crude on purpose: shadow mode runs on the request path, and the purpose is
    to catch gross divergence -- a truncated answer, a wrong file, an empty
    response -- not to adjudicate quality. Sets of words are used rather than
    order, since two correct answers rarely phrase things identically.
    """
    if not a.strip() or not b.strip():
        return False
    ta = set(re.findall(r"\w+", a.lower()))
    tb = set(re.findall(r"\w+", b.lower()))
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.5


class Stats:
    """Per-call counters and latency, so the cost claim is measurable.

    Latency is tracked because it is a first-class objective: a tier that
    halves spend while doubling p95 is not a win.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: dict[str, int] = defaultdict(int)
        self.errors: dict[str, int] = defaultdict(int)
        self.latency_ms: dict[str, list[float]] = defaultdict(list)

    def record(self, program: str, ms: float, ok: bool) -> None:
        with self._lock:
            self.calls[program] += 1
            self.latency_ms[program].append(ms)
            if not ok:
                self.errors[program] += 1

    def summary(self) -> dict:
        with self._lock:
            out = {}
            for program, n in sorted(self.calls.items()):
                lat = sorted(self.latency_ms[program])
                out[program] = {
                    "calls": n,
                    "errors": self.errors.get(program, 0),
                    "p50_ms": round(lat[len(lat) // 2], 1) if lat else 0.0,
                    "max_ms": round(lat[-1], 1) if lat else 0.0,
                }
            return out

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()
            self.errors.clear()
            self.latency_ms.clear()


class DspyRuntime:
    """Lazily-built holder for the five DSPy programs.

    Construction is cheap and does not touch the network: `configure_lms` and
    schema introspection only happen on first use, so a process that never
    routes a question pays nothing.
    """

    _instance: DspyRuntime | None = None
    _instance_lock = threading.Lock()

    def __init__(self, tier: str | None = None, shadow: bool | None = None):
        from src.backend.dspy_bridge.config import tier_for

        self.tier = tier_for(tier)
        # Shadow is opt-in, parsed as a bool. Two reasons it is not defaulted
        # on: reading the raw env string would make "0" truthy and therefore
        # impossible to turn off, and a shadow run makes a real model call per
        # request whose result is discarded. Turning that on by default would
        # bill every request for nothing, which is the opposite of what the
        # default configuration should do.
        self.shadow = (
            _truthy(os.getenv("ASK_DSPY_SHADOW"))
            if shadow is None
            else bool(shadow)
        )
        self._programs: dict = {}
        self._lock = threading.Lock()
        self.stats = Stats()
        self._shadow_agreement: dict[str, list[bool]] = defaultdict(list)

    @classmethod
    def get(cls) -> DspyRuntime:
        """Process-wide singleton.

        Programs hold no per-request state, and building them twice would
        double the schema introspection query on every chat request.
        """
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def enabled_flag(self) -> bool:
        """Whether ASK_DSPY opts in. Unset means off."""
        return _truthy(os.getenv("ASK_DSPY"))

    @property
    def enabled(self) -> bool:
        """Whether DSPy should serve answers at all.

        `ASK_DSPY=1` opts in. Unset means off, so the default install behaves
        exactly as it did before this module existed.
        """
        return self.enabled_flag

    # ── program access ──────────────────────────────────────────────────────
    def _build(self) -> dict:
        with self._lock:
            if self._programs:
                return self._programs
            from src.backend.dspy_bridge.config import bind_models, configure_lms
            from src.backend.dspy_bridge.programs import build_programs

            configure_lms(self.tier)
            programs = build_programs()
            # Must happen after build_programs: binding sets an attribute on the
            # predictors those calls create.
            bind_models(programs, self.tier)
            self._load_compiled(programs)
            self._programs = programs
            logger.info(
                "DSPy programs ready (tier=%s, shadow=%s, enabled=%s)",
                self.tier, self.shadow, self.enabled,
            )
            return programs

    def _load_compiled(self, programs: dict) -> None:
        """Attach saved optimizers, if any exist.

        Failure here is not fatal: an unoptimised program still works, so a
        missing or stale artefact degrades to the hand-written instructions
        rather than to an exception.
        """
        for name, program in programs.items():
            path = SAVED_DIR / f"{name}.json"
            if not path.exists():
                continue
            try:
                program.load(path)
                logger.info("loaded compiled program: %s", name)
            except Exception as e:
                logger.warning(
                    "could not load %s (%s: %s); using hand-written instructions",
                    path.name, type(e).__name__, e,
                )

    def program(self, name: str):
        return self._build().get(name)

    # ── call wrapper ────────────────────────────────────────────────────────
    def _call(self, name: str, fn):
        """Run a program, timing it, converting any failure into `None`.

        Shadow mode runs the program and then discards the result, so the
        legacy path still serves the answer. That is the whole point: it
        collects evidence about whether cutting over is safe, without
        changing what any user sees.

        Shadow mode does *not* require ASK_DSPY=1. Gating it on `enabled` --
        which is what this function used to do -- meant shadow mode could never
        run at all, since `enabled` defaults to false and shadow is off too.
        """
        if self.shadow:
            self._invoke(name, fn)
            return None
        if not self.enabled:
            return None
        return self._invoke(name, fn)

    def _invoke(self, name: str, fn):
        """Actually call the program. Returns None on any failure."""
        program = self.program(name)
        if program is None:
            return None
        start = time.perf_counter()
        try:
            out = fn(program)
            self.stats.record(name, (time.perf_counter() - start) * 1000, True)
            return out
        except Exception as e:
            self.stats.record(name, (time.perf_counter() - start) * 1000, False)
            logger.warning(
                "DSPy %s failed (%s: %s); falling back",
                name, type(e).__name__, e,
            )
            return None

    def record_agreement(self, program: str, agreed: bool) -> None:
        self._shadow_agreement[program].append(agreed)

    def compare(self, program: str, legacy: object, shadowed: object,
                reference: str | None = None) -> None:
        """Record how a shadowed DSPy result compares to the legacy one.

        Only does anything when shadow mode is on. In shadow mode the legacy
        path always serves the answer, so this is the only place the DSPy
        output is ever inspected -- and the only evidence available for
        deciding whether to cut over.

        Agreement is deliberately a cheap lexical check rather than another
        model call. A shadow run already costs a full DSPy call per request;
        adding a judge on top would make the measurement cost more than the
        thing it measures, and an LLM judge would be judging a cheap model's
        output with another cheap model.
        """
        if not self.shadow:
            return
        agreed = _similar(str(legacy or ""), str(shadowed or ""))
        self.record_agreement(program, agreed)
        if not agreed:
            logger.info(
                "[shadow] %s diverged (legacy %.0f chars vs dspy %.0f)%s",
                program, len(str(legacy or "")), len(str(shadowed or "")),
                f" ref={reference[:40]!r}" if reference else "",
            )

    def shadow_report(self) -> dict:
        out = {}
        for program, hits in self._shadow_agreement.items():
            out[program] = {
                "compared": len(hits),
                "agree_rate": round(sum(hits) / len(hits), 3) if hits else None,
            }
        return out

    def report(self) -> dict:
        return {
            "enabled": self.enabled,
            "tier": self.tier,
            "shadow": self.shadow,
            "stats": self.stats.summary(),
            "shadow_agreement": self.shadow_report(),
        }

    # ── the five operations ─────────────────────────────────────────────────
    def route(self, question: str):
        """-> (route, reason) or None. Route is graph|architecture|hybrid.

        The enabled check lives in _call, which also handles shadow mode.
        Duplicating it here would mean shadow mode could never produce a
        result to compare.
        """
        out = self._call("route", lambda p: p(question=question))
        if out is None:
            return None
        route = (getattr(out, "route", "") or "").strip().lower()
        # Normalise the aliases the old pydantic router allowed, so the caller
        # sees the same three values regardless of which backend answered.
        mapping = {
            "graph_only": "graph",
            "architecture": "architecture",
            "hybrid": "hybrid",
            "graph": "graph",
        }
        if route not in mapping:
            logger.warning("DSPy router returned unknown route %r", route)
            return None
        return mapping[route], getattr(out, "reason", "")

    def rewrite(self, question: str, history: str):
        out = self._call("rewrite", lambda p: p(question=question, history=history))
        if out is None:
            return None
        text = (getattr(out, "standalone_question", "") or "").strip()
        return text or None

    def cypher(self, question: str, repo_id: str):
        out = self._call("cypher", lambda p: p(question=question, repo_id=repo_id))
        if out is None:
            return None
        return (getattr(out, "cypher", "") or "").strip() or None

    def synthesize(self, question: str, context: str, history: str = ""):
        out = self._call(
            "synthesize",
            lambda p: p(question=question, context=context, history=history),
        )
        if out is None:
            return None
        answer = (getattr(out, "answer", "") or "").strip()
        if not answer:
            return None
        try:
            confidence = float(getattr(out, "confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        files = (getattr(out, "used_files", "") or "").strip()
        return {
            "answer": answer,
            "score": max(0.0, min(1.0, confidence)),
            "sources": files,
        }

    def verify(self, question: str, context: str, draft: str):
        """-> (verdict, worst_gap) or None."""
        out = self._call(
            "verify",
            lambda p: p(question=question, context=context, draft=draft),
        )
        if out is None:
            return None
        verdict = (getattr(out, "verdict", "") or "").strip().lower()
        if verdict not in ("supported", "unsupported", "off_topic"):
            return None
        return verdict, (getattr(out, "worst_gap", "") or "").strip()
