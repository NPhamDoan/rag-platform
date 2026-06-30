"""Shared utility to mask sensitive fields when formatting logs.

R14.4 / R22.3: every log entry SHALL exclude or mask sensitive fields (password,
token, API key, full personal information). This utility is shared everywhere
structured data (nested dict/list) needs to be logged, masking at EVERY nesting
level per the steering (not just the outermost level).

Convention: the `mask_sensitive` function returns a new copy (does not mutate the
original input) with the value of every sensitive key replaced by
`MASK_PLACEHOLDER`.
"""

from __future__ import annotations

from typing import Any

# Replacement value for a sensitive field.
MASK_PLACEHOLDER = "***"

# Set of key hints (substring, compared after lowercasing) treated as sensitive.
# Covers passwords, tokens, API keys, secrets, and full personal information (PII).
SENSITIVE_KEY_PARTS: frozenset[str] = frozenset(
    {
        # Password
        "password",
        "matkhau",
        "matkhauhash",
        "pwd",
        # Token / session
        "token",
        "authorization",
        "jti",
        "secret",
        # API key / encryption
        "apikey",
        "api_key",
        "khoaapi",
        "khoamahoa",
        "khoa",
        "secretkey",
        "secret_key",
        # PII
        "email",
        "sodienthoai",
        "phone",
    }
)


def _is_sensitive_key(key: str) -> bool:
    """Decide whether a key is sensitive (substring match, ignoring case and '_')."""
    normalized = key.lower().replace("_", "").replace("-", "")
    return any(part.replace("_", "") in normalized for part in SENSITIVE_KEY_PARTS)


def mask_sensitive(data: Any) -> Any:
    """Mask the value of every sensitive field at EVERY nesting level.

    - dict: for each sensitive key → replace the value with MASK_PLACEHOLDER; the
      remaining keys are traversed recursively.
    - list/tuple/set: recurse into each element (returns a list for consistency).
    - other types (str, int, ...): returned unchanged.

    Does not mutate the original `data`; always returns a new structure.
    """
    if isinstance(data, dict):
        masked: dict[Any, Any] = {}
        for key, value in data.items():
            if isinstance(key, str) and _is_sensitive_key(key):
                masked[key] = MASK_PLACEHOLDER
            else:
                masked[key] = mask_sensitive(value)
        return masked
    if isinstance(data, (list, tuple, set)):
        return [mask_sensitive(item) for item in data]
    return data
