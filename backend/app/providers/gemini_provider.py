"""Gemini LLM_Provider — registration STUB (task 1.5).

Self-registers via `@register_llm("gemini")`; the concrete implementation (calling the
Gemini API) is added in a later provider implementation task. This keeps a skeleton so
the configuration `LLM_VERIFY_PROVIDER=gemini` (the verification role, and
normalization when left blank — R13.4) passes the fail-fast check at startup.
"""

from __future__ import annotations

from app.providers.registry import register_llm


@register_llm("gemini")
class GeminiProvider:
    """Gemini LLM_Provider stub (default verification role). Implementation in a later task."""

    ten = "gemini"
