"""
Fallback LLM provider: tries OpenAI first, falls back to Groq on failure.
Logs all errors, failures, and fallbacks to terminal with full stack traces.
"""
import logging
import traceback
import sys
import time

logger = logging.getLogger('askmyrepo.llm')

# OpenAI error types we catch for fallback
OPENAI_FAILURE_KEYWORDS = [
    "insufficient_quota",
    "rate_limit",
    "exceeded",
    "capacity",
    "429",
    "503",
    "api key",
    "authentication",
    "unauthorized",
    "deactivated",
    "billing",
    "credit",
    "expired",
    "token",
    "server error",
]


def _is_openai_failure(exception: Exception) -> bool:
    """Check if a.n exception indicates an OpenAI failure that should trigger fallback."""
    msg = str(exception).lower()
    exc_name = type(exception).__name__.lower()

    for keyword in OPENAI_FAILURE_KEYWORDS:
        if keyword in msg or keyword in exc_name:
            return True
    return False


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
        from langchain_groq import ChatGroq

        self.openai = ChatOpenAI(
            model="gpt-4o",
            temperature=0,
            max_tokens=1000,
            max_retries=2,
            timeout=30.0,
        )
        self.groq = ChatGroq(
            model="llama-3.3-70b-versatile",
            temperature=0,
            max_tokens=2000,
            timeout=60.0,
        )
        self._fallback_used = False
        self._last_error = None

    def with_structured_output(self, schema, **kwargs):
        return FallbackStructuredOutput(self, schema, kwargs)

    @property
    def model_name(self):
        return self.groq.model if self._fallback_used else self.openai.model

    def invoke(self, inputs, **kwargs):
        return self._invoke_with_fallback(self.openai.invoke, self.groq.invoke, inputs, kwargs)

    def _invoke_with_fallback(self, primary_fn, fallback_fn, inputs, kwargs):
        try:
            self._fallback_used = False
            return primary_fn(inputs, **kwargs)
        except Exception as e:
            self._last_error = e
            exc_type = type(e).__name__
            exc_msg = str(e)
            logger.error("=" * 60)
            logger.error(f"[OPENAI FAILURE] Model: gpt-4o")
            logger.error(f"[OPENAI FAILURE] Type: {exc_type}")
            logger.error(f"[OPENAI FAILURE] Message: {exc_msg}")
            logger.error(f"[OPENAI FAILURE] Traceback:")
            for line in traceback.format_exc().splitlines():
                logger.error(f"  {line}")
            logger.error("-" * 60)

            if _is_openai_failure(e):
                logger.warning(f"[FALLBACK] Switching to Groq (llama-3.3-70b-versatile)...")
                self._fallback_used = True
                try:
                    return fallback_fn(inputs, **kwargs)
                except Exception as e2:
                    logger.error(f"[GROQ ALSO FAILED] {type(e2).__name__}: {e2}")
                    for line in traceback.format_exc().splitlines():
                        logger.error(f"  {line}")
                    raise
            else:
                logger.info("[FALLBACK SKIPPED] Error is not an OpenAI API failure — re-raising.")
                raise

    def __getattr__(self, name):
        """Delegate any unimplemented attributes to the active LLM."""
        if name in ('_fallback_used', '_last_error', 'openai', 'groq', 'with_structured_output', 'invoke', '_invoke_with_fallback'):
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
        self.groq_runnable = parent.groq.with_structured_output(schema, **kwargs)

    def invoke(self, inputs, **kwargs):
        try:
            self.parent._fallback_used = False
            return self.openai_runnable.invoke(inputs, **kwargs)
        except Exception as e:
            self.parent._last_error = e
            exc_type = type(e).__name__
            exc_msg = str(e)
            logger.error("=" * 60)
            logger.error(f"[OPENAI STRUCTURED FAILURE] Model: gpt-4o")
            logger.error(f"[OPENAI STRUCTURED FAILURE] Type: {exc_type}")
            logger.error(f"[OPENAI STRUCTURED FAILURE] Message: {exc_msg}")
            logger.error(f"[OPENAI STRUCTURED FAILURE] Traceback:")
            for line in traceback.format_exc().splitlines():
                logger.error(f"  {line}")
            logger.error("-" * 60)

            if _is_openai_failure(e):
                logger.warning(f"[FALLBACK] Switching to Groq structured output (llama-3.3-70b-versatile)...")
                self.parent._fallback_used = True
                try:
                    return self.groq_runnable.invoke(inputs, **kwargs)
                except Exception as e2:
                    logger.error(f"[GROQ STRUCTURED ALSO FAILED] {type(e2).__name__}: {e2}")
                    for line in traceback.format_exc().splitlines():
                        logger.error(f"  {line}")
                    raise
            else:
                logger.info("[FALLBACK SKIPPED] Error is not an OpenAI API failure — re-raising.")
                raise
