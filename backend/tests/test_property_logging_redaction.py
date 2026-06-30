"""Property-based test for the sensitive-field redaction util used when logging (R14.4 / R22.3).

# Feature: multi-user-rag-platform, Property 56: Log loai tru/che moi truong nhay cam
#   For any nested dict/list structure: no sensitive value remains in the redacted result
#   at ANY depth; non-sensitive values are preserved unchanged; and the original input is
#   not modified.
"""

from __future__ import annotations

import copy
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from app.logging_redaction import MASK_PLACEHOLDER, _is_sensitive_key, mask_sensitive

# DEFINITELY sensitive keys (substring match in SENSITIVE_KEY_PARTS).
SENSITIVE_KEYS = [
    "password",
    "matKhau",
    "matKhauHash",
    "token",
    "authorization",
    "jti",
    "secret",
    "apiKey",
    "api_key",
    "khoaApi",
    "khoaMaHoa",
    "secretKey",
    "email",
    "soDienThoai",
    "phone",
    "userPassword",  # substring 'password' → still sensitive
]

# DEFINITELY non-sensitive keys (do not contain any sensitive substring).
SAFE_KEYS = [
    "ten",
    "id",
    "soChunk",
    "noiDung",
    "thuTu",
    "active",
    "items",
    "user",
    "profile",
    "khongGianId",
    "trangThai",
    "data",
]

# Sentinel to trace values through the redaction process.
SENSITIVE_MARK = "SENSITIVE::"
SAFE_MARK = "SAFE::"

# Guard for the correctness of the sample key sets (not testing the implementation, just
# ensuring the generator uses the right assumptions about "sensitive" / "non-sensitive").
assert all(_is_sensitive_key(k) for k in SENSITIVE_KEYS)
assert all(not _is_sensitive_key(k) for k in SAFE_KEYS)


# Sensitive value: only appears as the DIRECT VALUE of a sensitive key.
sensitive_values = st.builds(lambda n: f"{SENSITIVE_MARK}{n}", st.integers(0, 1_000_000))

# Safe leaves: traceable strings + other primitive types.
safe_leaves = st.one_of(
    st.builds(lambda n: f"{SAFE_MARK}{n}", st.integers(0, 1_000_000)),
    st.integers(),
    st.booleans(),
    st.none(),
)


def _safe_node(max_depth: int) -> st.SearchStrategy[Any]:
    """Generate a 'safe' nested structure: sensitive values appear ONLY under sensitive keys."""
    if max_depth <= 0:
        return safe_leaves

    child = _safe_node(max_depth - 1)

    def build_dict(
        sens_keys: list[str], safe_pairs: list[tuple[str, Any]], sens_vals: list[str]
    ) -> dict[str, Any]:
        node: dict[str, Any] = {}
        for key, value in safe_pairs:
            node[key] = value
        for key, value in zip(sens_keys, sens_vals):
            node[key] = value  # sensitive key → sensitive sentinel value
        return node

    dict_strategy = st.builds(
        build_dict,
        st.lists(st.sampled_from(SENSITIVE_KEYS), max_size=4, unique=True),
        st.lists(st.tuples(st.sampled_from(SAFE_KEYS), child), max_size=4),
        st.lists(sensitive_values, max_size=4),
    )
    list_strategy = st.lists(child, max_size=4)

    return st.one_of(safe_leaves, dict_strategy, list_strategy)


nested_structures = _safe_node(max_depth=4)


def _collect_string_leaves(data: Any, out: list[str]) -> None:
    """Collect every leaf string value in the structure, at every nesting level."""
    if isinstance(data, dict):
        for value in data.values():
            _collect_string_leaves(value, out)
    elif isinstance(data, (list, tuple, set)):
        for item in data:
            _collect_string_leaves(item, out)
    elif isinstance(data, str):
        out.append(data)


def _assert_sensitive_keys_masked(data: Any) -> None:
    """Every sensitive key at every level MUST have a value == MASK_PLACEHOLDER."""
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and _is_sensitive_key(key):
                assert value == MASK_PLACEHOLDER
            else:
                _assert_sensitive_keys_masked(value)
    elif isinstance(data, (list, tuple, set)):
        for item in data:
            _assert_sensitive_keys_masked(item)


# Feature: multi-user-rag-platform, Property 56: Log loai tru/che moi truong nhay cam
# Validates: Requirements 14.4, 22.3
@settings(max_examples=40)
@given(nested_structures)
def test_property_mask_sensitive_redacts_all_levels(data: Any) -> None:
    original_snapshot = copy.deepcopy(data)

    masked = mask_sensitive(data)

    masked_leaves: list[str] = []
    _collect_string_leaves(masked, masked_leaves)

    # 1) No sensitive value remains at ANY depth.
    assert all(not leaf.startswith(SENSITIVE_MARK) for leaf in masked_leaves)

    # 1b) Every sensitive key is redacted with MASK_PLACEHOLDER.
    _assert_sensitive_keys_masked(masked)

    # 2) Every non-sensitive value (safe sentinel) is preserved, with the right count.
    safe_in_original: list[str] = []
    _collect_string_leaves(original_snapshot, safe_in_original)
    safe_expected = sorted(s for s in safe_in_original if s.startswith(SAFE_MARK))
    safe_actual = sorted(s for s in masked_leaves if s.startswith(SAFE_MARK))
    assert safe_actual == safe_expected

    # 3) The original input is NOT modified.
    assert data == original_snapshot
