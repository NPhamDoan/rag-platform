"""LLM_Provider interface (R13).

The minimal contract for every LLM_Provider that self-registers via `@register_llm`:
generate text from a (systemPrompt, userPrompt) pair. This is the contract the pipelines
(query: normalization / synthesis / verification) depend on; the concrete provider
implementations (groq/gemini/ollama) are added in later provider tasks, while tests
inject a fake provider compatible with this contract.

Uses `Protocol` (duck-typing) instead of a base class that must be inherited — keeping
the registry's self-registration pattern, in parallel with `EmbeddingProvider`
(`embedding_provider.py`): a provider only needs a `ten` attribute + a
`generate(systemPrompt, userPrompt)` method to be compatible (no inheritance-tree
constraint).

The `generate` contract:
- `systemPrompt`: the role instruction (synthesis / verification / normalization), may
  be empty.
- `userPrompt`: the actual input content (e.g. the question to add diacritics to).
- Returns: the text the LLM generates (a string). Never returns `None`; provider call
  errors (network/timeout/quota) raise an exception so the caller can decide on a
  fallback — does NOT swallow errors silently.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """LLM_Provider contract: `ten` + `generate(systemPrompt, userPrompt)`.

    `runtime_checkable` allows `isinstance(x, LLMProvider)` to check for a `generate`
    method (useful when injecting a fake provider in tests).
    """

    #: Provider name registered in LLM_REGISTRY (matches `.env`).
    ten: str

    def generate(self, systemPrompt: str, userPrompt: str) -> str:
        """Generate text from (systemPrompt, userPrompt); return the result string."""
        ...
