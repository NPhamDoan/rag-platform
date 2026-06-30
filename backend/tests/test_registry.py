"""Unit + property tests for the self-registering registry + fail-fast init (task 1.5).

Coverage:
- Decorator registration + lookup for LLM/Embedding/Chunker (R13.1, R17.1, R21.1).
- Auto-discover: adding a new `*_provider.py` file → auto-loaded into the registry, with
  NO change to the lookup function (R13.2, R21.2). Proven by dropping a temp module into
  the package.
- Lookup of a non-existent name → an error naming the missing name (R13.3, R17.7, R21.3).
- Fail-fast `validate_provider_config`: a missing/non-existent provider for a required role
  → InitializationError naming the role; an empty normalize role is valid (R13.4, R13.5).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from app.config import Settings
from app.errors import InitializationError, ValidationError
from app.chunking import registry as chunk_reg
from app.providers import registry as prov_reg


# --- Helper -----------------------------------------------------------------
def _make_settings(**overrides) -> Settings:
    """Build Settings with valid default provider config, allowing per-field overrides."""
    base = dict(
        llm_primary_provider="groq",
        llm_verify_provider="gemini",
        llm_normalize_provider="",
        embedding_provider="huggingface",
    )
    base.update(overrides)
    return Settings(**base)


# Load the provider/chunker stubs once at module level: the import has side effects, and
# subsequent imports are cached so the decorator does not run again. As a result, the
# snapshot in the fixture already includes the default stubs (groq/gemini/huggingface)
# and the restore keeps them.
prov_reg.discover_providers()
chunk_reg.discover_chunkers()


@pytest.fixture(autouse=True)
def _restore_registries():
    """Restore the registry to the snapshot after each test (drop providers added by tests)."""
    llm_backup = dict(prov_reg.LLM_REGISTRY)
    emb_backup = dict(prov_reg.EMBEDDING_REGISTRY)
    chunk_backup = dict(chunk_reg.CHUNKER_REGISTRY)
    yield
    prov_reg.LLM_REGISTRY.clear()
    prov_reg.LLM_REGISTRY.update(llm_backup)
    prov_reg.EMBEDDING_REGISTRY.clear()
    prov_reg.EMBEDDING_REGISTRY.update(emb_backup)
    chunk_reg.CHUNKER_REGISTRY.clear()
    chunk_reg.CHUNKER_REGISTRY.update(chunk_backup)


# --- Decorator registration + lookup --------------------------------------------
def test_register_and_lookup_llm_provider():
    @prov_reg.register_llm("test-llm")
    class _Llm:
        pass

    assert prov_reg.get_llm_provider("test-llm") is _Llm


def test_register_and_lookup_embedding_provider():
    @prov_reg.register_embedding("test-emb")
    class _Emb:
        pass

    assert prov_reg.get_embedding_provider("test-emb") is _Emb


def test_register_and_lookup_chunker():
    @chunk_reg.register_chunker("test-chunk")
    class _Chunk:
        pass

    assert chunk_reg.get_chunker("test-chunk") is _Chunk


def test_register_empty_name_rejected():
    with pytest.raises(ValueError):
        prov_reg.register_llm("   ")
    with pytest.raises(ValueError):
        chunk_reg.register_chunker("")


# --- Lookup of a non-existent name → an error naming the name -----------------------------
def test_unknown_llm_provider_raises_named_error():
    with pytest.raises(InitializationError) as exc:
        prov_reg.get_llm_provider("khong-co-provider-nay")
    assert "khong-co-provider-nay" in exc.value.message


def test_unknown_embedding_provider_raises_named_error():
    with pytest.raises(InitializationError) as exc:
        prov_reg.get_embedding_provider("emb-la")
    assert "emb-la" in exc.value.message


def test_unknown_chunker_raises_named_validation_error():
    # R17.7: a non-existent strategy → an error naming it (chunk-time => ValidationError).
    with pytest.raises(ValidationError) as exc:
        chunk_reg.get_chunker("chien-luoc-la")
    assert "chien-luoc-la" in exc.value.message


# --- Auto-discover: adding a new file = auto-loaded, with NO change to the factory -----------------
def test_discover_loads_default_provider_stubs():
    prov_reg.discover_providers()
    assert "groq" in prov_reg.LLM_REGISTRY
    assert "gemini" in prov_reg.LLM_REGISTRY
    assert "huggingface" in prov_reg.EMBEDDING_REGISTRY


def test_discover_auto_loads_newly_added_provider_file():
    """Drop a new `*_provider.py` file into the package → discover auto-loads it, no change to lookup."""
    pkg_dir = Path(prov_reg.__file__).parent
    module_name = "zzz_tmp_test_provider"
    file_path = pkg_dir / f"{module_name}.py"
    file_path.write_text(
        "from app.providers.registry import register_llm\n"
        "@register_llm('zzz-tmp')\n"
        "class _ZzzTmp:\n"
        "    pass\n",
        encoding="utf-8",
    )
    try:
        # The newly added file → discover detects and loads it, with NO change to the lookup function.
        prov_reg.discover_providers()
        assert prov_reg.get_llm_provider("zzz-tmp").__name__ == "_ZzzTmp"
    finally:
        file_path.unlink(missing_ok=True)
        sys.modules.pop(f"app.providers.{module_name}", None)
        pyc = pkg_dir / "__pycache__"
        for f in pyc.glob(f"{module_name}.*.pyc"):
            f.unlink(missing_ok=True)


def test_discover_chunkers_idempotent():
    chunk_reg.discover_chunkers()
    snapshot = dict(chunk_reg.CHUNKER_REGISTRY)
    chunk_reg.discover_chunkers()
    assert chunk_reg.CHUNKER_REGISTRY == snapshot


# --- Fail-fast validate_provider_config -------------------------------------
def test_validate_passes_with_registered_providers():
    prov_reg.discover_providers()
    # No error when every required role has a valid provider.
    prov_reg.validate_provider_config(_make_settings())


def test_validate_empty_normalize_is_valid_uses_verify():
    # R13.4: an empty normalize role is VALID (uses the verification provider) → no error.
    prov_reg.discover_providers()
    prov_reg.validate_provider_config(_make_settings(llm_normalize_provider=""))


def test_validate_fails_when_primary_unknown_names_provider():
    prov_reg.discover_providers()
    with pytest.raises(InitializationError) as exc:
        prov_reg.validate_provider_config(_make_settings(llm_primary_provider="khong-ton-tai"))
    assert "khong-ton-tai" in exc.value.message


def test_validate_fails_when_embedding_unknown_names_provider():
    prov_reg.discover_providers()
    with pytest.raises(InitializationError) as exc:
        prov_reg.validate_provider_config(_make_settings(embedding_provider="emb-bia"))
    assert "emb-bia" in exc.value.message


def test_validate_fails_when_verify_role_empty_names_role():
    prov_reg.discover_providers()
    with pytest.raises(InitializationError) as exc:
        prov_reg.validate_provider_config(_make_settings(llm_verify_provider=""))
    # Names the verification role as not configured.
    assert "xac minh" in exc.value.message


# --- Task 1.8: a missing required role (empty) → fail-fast naming the role --
# Adds the "missing role" branches not covered in task 1.5: the synthesis role
# (R13.5) and the embedding role (R21.3) being empty → halt initialization, naming the role.
def test_validate_fails_when_primary_role_empty_names_role():
    # R13.5: the synthesis role is required; empty → halt initialization + name the role.
    prov_reg.discover_providers()
    with pytest.raises(InitializationError) as exc:
        prov_reg.validate_provider_config(_make_settings(llm_primary_provider=""))
    assert "tong hop" in exc.value.message


def test_validate_fails_when_embedding_role_empty_names_role():
    # R21.3 / R13.5: the embedding role is required; empty → halt initialization + name the role.
    prov_reg.discover_providers()
    with pytest.raises(InitializationError) as exc:
        prov_reg.validate_provider_config(_make_settings(embedding_provider=""))
    assert "embedding" in exc.value.message


def test_validate_fails_when_normalize_set_but_unknown():
    prov_reg.discover_providers()
    with pytest.raises(InitializationError) as exc:
        prov_reg.validate_provider_config(_make_settings(llm_normalize_provider="norm-la"))
    assert "norm-la" in exc.value.message


def test_validate_reports_all_problems_together():
    prov_reg.discover_providers()
    with pytest.raises(InitializationError) as exc:
        prov_reg.validate_provider_config(
            _make_settings(llm_primary_provider="x-bia", embedding_provider="y-bia")
        )
    assert "x-bia" in exc.value.message
    assert "y-bia" in exc.value.message


# --- Property test: an unregistered name is always rejected with the name named ------------
# Feature: multi-user-rag-platform, Property (task 1.5): a provider not present in the
# registry is always fail-fast with a message naming the provider.
_REGISTERED = {"groq", "gemini", "huggingface", ""}


@hyp_settings(max_examples=100)
@given(ten=st.text(min_size=1, max_size=20).filter(lambda s: s.strip() and s.strip() not in _REGISTERED))
def test_property_unknown_primary_always_fails_with_name(ten):
    prov_reg.discover_providers()
    with pytest.raises(InitializationError) as exc:
        prov_reg.validate_provider_config(_make_settings(llm_primary_provider=ten))
    assert ten.strip() in exc.value.message
