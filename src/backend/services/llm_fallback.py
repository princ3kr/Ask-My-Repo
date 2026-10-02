"""
Fallback LLM provider: tries OpenAI first, falls back to Groq on failure.
Logs all errors, failures, and fallbacks to terminal with full stack traces.
"""
import logging
import traceback

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('askmyrepo.llm')

# OpenAI error types we catch for fallback. Matched as substrings against the
# exception message and its class name, so each entry has to be specific enough
# not to fire on unrelated failures.
#
# "token" was in this list and is deliberately not: it matched "token limit",
# "max_tokens exceeded" and every other message mentioning tokens, so ordinary
# request-shape errors silently started spending the Groq key. Bare "exceeded"
# and "credit" were equally over-broad. Nor is "context length" here — Groq's
# window is smaller, so failing over on an overflow can only fail again. This
# list is restricted to availability and authorisation failures, which is where
# a second provider can actually help.
OPENAI_FAILURE_KEYWORDS = [
    # availability / capacity
    "insufficient_quota",
    "quota",
    "rate limit",
    "rate_limit",
    "overloaded",
    "server had an error",
    "server error",
    "server_error",
    "service unavailable",
    # authentication / billing
    "api key",
    "authentication",
    "unauthorized",
    "deactivated",
    "billing",
    "credit balance",
]


def _is_openai_failure(exception: Exception) -> bool:
    """Check if a.n exception indicates an OpenAI failure that should trigger fallback."""
    msg = str(exception).lower()
    exc_name = type(exception).__name__.lower()

    return any(keyword in msg or keyword in exc_name for keyword in OPENAI_FAILURE_KEYWORDS)


class FallbackChatModel:
    """
    Wraps ChatOpenAI with automatic fallback to ChatGroq.
    Every failure is logged to terminal with:
    - Timestamp
    - Error type and message
    - Full stack trace
    - Which model is being used as fallback
    """

    def __init__(self):
        from langchain_openai import ChatOpenAI

        self.openai = ChatOpenAI(
            model="gpt-4o",
            temperature=0,
            max_tokens=1000,
            max_retries=2,
            timeout=30.0,
        )
        self._groq = None
        # Monotonic, not a boolean. This was reset to False at the start of
        # every invoke(), so a request where only the router fell back reported
        # "no fallback" to the API, because the last call (the synthesizer) had
        # succeeded on OpenAI. The instance is per-session and requests are
        # sequential, so a counter cannot interleave.
        self._fallback_count = 0
        self._last_error = None

    @property
    def _fallback_used(self) -> bool:
        """True if *any* call on this instance ever fell back."""
        return self._fallback_count > 0

    @property
    def groq(self):
        if self._groq is None:
            from langchain_groq import ChatGroq

            self._groq = ChatGroq(
                model="llama-3.3-70b-versatile",
                temperature=0,
                max_tokens=2000,
                timeout=60.0,
            )
        return self._groq

    def with_structured_output(self, schema, **kwargs):
        from langchain_core.runnables import RunnableLambda
        inner = FallbackStructuredOutput(self, schema, kwargs)
        return RunnableLambda(inner.invoke)

    @property
    def model_name(self):
        return self.groq.model if self._fallback_used else self.openai.model

    def invoke(self, inputs, **kwargs):
        return self._invoke_with_fallback(self.openai.invoke, self.groq.invoke, inputs, kwargs)

    def _invoke_with_fallback(self, primary_fn, fallback_fn, inputs, kwargs):
        try:
            return primary_fn(inputs, **kwargs)
        except Exception as e:
            self._last_error = e
            exc_type = type(e).__name__
            exc_msg = str(e)
            logger.error("=" * 60)
            logger.error("[OPENAI FAILURE] Model: gpt-4o")
            logger.error(f"[OPENAI FAILURE] Type: {exc_type}")
            logger.error(f"[OPENAI FAILURE] Message: {exc_msg}")
            logger.error("[OPENAI FAILURE] Traceback:")
            for line in traceback.format_exc().splitlines():
                logger.error(f"  {line}")
            logger.error("-" * 60)

            if _is_openai_failure(e):
                logger.warning("[FALLBACK] Switching to Groq (llama-3.3-70b-versatile)...")
                self._fallback_count += 1
                try:
                    return fallback_fn(inputs, **kwargs)
                except Exception as e2:
                    logger.error(f"[GROQ ALSO FAILED] {type(e2).__name__}: {e2}")
                    for line in traceback.format_exc().splitlines():
                        logger.error(f"  {line}")
                    raise RuntimeError(
                        "Both LLM providers failed. "
                        f"OpenAI ({type(e).__name__}): {e} | "
                        f"Groq ({type(e2).__name__}): {e2}"
                    ) from e2
            else:
                logger.info("[FALLBACK SKIPPED] Error is not an OpenAI API failure — re-raising.")
                raise

    def __getattr__(self, name):
        """Delegate any unimplemented attributes to the active LLM.

        Only reached when normal lookup fails, so this just has to avoid
        recursing back into self for the attributes defined on the class.
        """
        if name.startswith("_") or name in (
            "openai", "groq", "with_structured_output", "invoke",
        ):
            raise AttributeError(name)
        return getattr(self.groq if self._fallback_used else self.openai, name)


class FallbackStructuredOutput:
    """
    Wraps structured output (e.g., with_structured_output) with fallback support.
    Both OpenAI and Groq get their own structured output runnable.
    """

    def __init__(self, parent: FallbackChatModel, schema, kwargs):
        self.parent = parent
        self.openai_runnable = parent.openai.with_structured_output(schema, **kwargs)
        self.schema = schema
        self.kwargs = kwargs
        self.groq_runnable = None

    def invoke(self, inputs, **kwargs):
        try:
            return self.openai_runnable.invoke(inputs, **kwargs)
        except Exception as e:
            self.parent._last_error = e
            exc_type = type(e).__name__
            exc_msg = str(e)
            logger.error("=" * 60)
            logger.error("[OPENAI STRUCTURED FAILURE] Model: gpt-4o")
            logger.error(f"[OPENAI STRUCTURED FAILURE] Type: {exc_type}")
            logger.error(f"[OPENAI STRUCTURED FAILURE] Message: {exc_msg}")
            logger.error("[OPENAI STRUCTURED FAILURE] Traceback:")
            for line in traceback.format_exc().splitlines():
                logger.error(f"  {line}")
            logger.error("-" * 60)

            if _is_openai_failure(e):
                logger.warning("[FALLBACK] Switching to Groq structured output (llama-3.3-70b-versatile)...")
                self.parent._fallback_count += 1
                try:
                    if self.groq_runnable is None:
                        self.groq_runnable = self.parent.groq.with_structured_output(
                            self.schema, **self.kwargs
                        )
                    return self.groq_runnable.invoke(inputs, **kwargs)
                except Exception as e2:
                    logger.error(f"[GROQ STRUCTURED ALSO FAILED] {type(e2).__name__}: {e2}")
                    for line in traceback.format_exc().splitlines():
                        logger.error(f"  {line}")
                    raise RuntimeError(
                        "Both LLM providers failed. "
                        f"OpenAI ({type(e).__name__}): {e} | "
                        f"Groq ({type(e2).__name__}): {e2}"
                    ) from e2
            else:
                logger.info("[FALLBACK SKIPPED] Error is not an OpenAI API failure — re-raising.")
                raise
