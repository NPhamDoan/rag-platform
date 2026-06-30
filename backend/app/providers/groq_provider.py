"""Groq LLM_Provider — registration STUB (task 1.5).

This file illustrates the self-registration pattern: just existing + the `@register_llm`
decorator is enough for `discover_providers()` to load it into the registry, with NO
edits to the lookup function/factory.

The concrete implementation (calling the Groq API, build_from settings/key/timeout) is
added in a later provider implementation task (inherited from Vietnam Law RAG). For now
it only keeps a registration skeleton so the configuration `LLM_PRIMARY_PROVIDER=groq`
passes the fail-fast check at startup.
"""

from __future__ import annotations

from app.providers.registry import register_llm


@register_llm("groq")
class GroqProvider:
    """Groq LLM_Provider stub (default synthesis role). Implementation in a later task."""

    ten = "groq"
