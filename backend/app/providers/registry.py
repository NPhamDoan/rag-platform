"""Self-registering registry for LLM_Provider and Embedding_Provider (R13, R21).

Pattern (reusing the mechanism from the Vietnam Law RAG codebase):
- Each provider class registers itself into the registry with the
  `@register_llm("ten")` or `@register_embedding("ten")` decorator.
- At startup, `discover_providers()` automatically imports EVERY `*_provider.py` /
  `*_embedding.py` file in this package → the decorators run → the registry is fully
  populated.
- Lookups go through `get_llm_provider(ten)` / `get_embedding_provider(ten)`.

=> ADDING A NEW PROVIDER = create a `*_provider.py` file + decorator, do NOT edit the
lookup function or any core component (R13.2, R21.2).

Fail-fast at startup (`validate_provider_config`): if the configuration specifies a
provider not present in the registry, or a required role (synthesis/verification/
embedding) is left blank → raise `InitializationError` with a clear name, halting
startup (R13.3, R13.5, R21.3). A blank normalization role is VALID → it uses the
verification provider (R13.4).

Logs through the centralized logger; does not swallow errors silently.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Callable, TypeVar

from app.config import Settings
from app.errors import InitializationError

logger = logging.getLogger(__name__)

# Registry: provider name (config) -> self-registered provider class.
LLM_REGISTRY: dict[str, type] = {}
EMBEDDING_REGISTRY: dict[str, type] = {}

# File suffixes treated as provider modules during auto-discovery.
_PROVIDER_SUFFIXES = ("_provider", "_embedding")

T = TypeVar("T")


def _register(registry: dict[str, type], loai: str, ten: str) -> Callable[[type], type]:
    """Build a decorator that registers `cls` into `registry` under the key `ten`."""
    ten_chuan = ten.strip()
    if not ten_chuan:
        raise ValueError(f"Ten {loai} provider khi dang ky khong duoc de trong")

    def decorator(cls: type) -> type:
        existing = registry.get(ten_chuan)
        if existing is not None and existing is not cls:
            # Don't swallow silently: warn when two files register the same name.
            logger.warning(
                "Ghi de dang ky %s provider '%s': %s -> %s",
                loai,
                ten_chuan,
                existing.__name__,
                cls.__name__,
            )
        registry[ten_chuan] = cls
        logger.debug("Dang ky %s provider '%s' -> %s", loai, ten_chuan, cls.__name__)
        return cls

    return decorator


def register_llm(ten: str) -> Callable[[type], type]:
    """Decorator that registers an LLM_Provider under `ten` (used in `.env`)."""
    return _register(LLM_REGISTRY, "LLM", ten)


def register_embedding(ten: str) -> Callable[[type], type]:
    """Decorator that registers an Embedding_Provider under `ten` (used in `.env`)."""
    return _register(EMBEDDING_REGISTRY, "Embedding", ten)


def _discover_in_package(package_name: str) -> list[str]:
    """Import every provider module in `package_name`; return the loaded module names.

    Scans the modules directly inside the package, importing only files with a provider
    suffix (`*_provider.py` / `*_embedding.py`). The import triggers the decorator → self-registration.
    """
    package = importlib.import_module(package_name)
    discovered: list[str] = []
    for module_info in pkgutil.iter_modules(package.__path__):
        name = module_info.name
        if name.startswith("_") or name == "registry":
            continue
        if not name.endswith(_PROVIDER_SUFFIXES):
            continue
        importlib.import_module(f"{package_name}.{name}")
        discovered.append(name)
    return discovered


def discover_providers() -> None:
    """Automatically discover + load every provider file in this package (R13.2, R21.2)."""
    discovered = _discover_in_package(__package__)
    logger.info(
        "Nap registry provider: %d module (%s); LLM=%s; Embedding=%s",
        len(discovered),
        ", ".join(sorted(discovered)) or "(khong co)",
        sorted(LLM_REGISTRY),
        sorted(EMBEDDING_REGISTRY),
    )


def get_llm_provider(ten: str) -> type:
    """Look up an LLM_Provider class by `ten`; if absent → InitializationError with a clear name."""
    cls = LLM_REGISTRY.get(ten.strip())
    if cls is None:
        raise InitializationError(
            f"LLM provider '{ten}' khong ton tai trong registry",
            details={"available": sorted(LLM_REGISTRY)},
        )
    return cls


def get_embedding_provider(ten: str) -> type:
    """Look up an Embedding_Provider class by `ten`; if absent → InitializationError."""
    cls = EMBEDDING_REGISTRY.get(ten.strip())
    if cls is None:
        raise InitializationError(
            f"Embedding provider '{ten}' khong ton tai trong registry",
            details={"available": sorted(EMBEDDING_REGISTRY)},
        )
    return cls


def validate_provider_config(settings: Settings) -> None:
    """Fail-fast validation of the provider configuration at startup (R13.3, R13.5, R21.3).

    Rules:
    - synthesis (`llm_primary_provider`) + verification (`llm_verify_provider`):
      required, blank → error with a clear role; a name not in the registry → error with
      a clear name.
    - normalization (`llm_normalize_provider`): blank is VALID (uses the verification
      provider — R13.4); if a name is given but does not exist → error with a clear name.
    - embedding (`embedding_provider`): required; blank/name not in the registry → error
      with a clear role/name.

    Collects all problems and raises ONE `InitializationError` listing them in full, so
    the operator can fix them in a single pass. No collected problems → continue startup.
    """
    problems: list[str] = []

    def _check_llm_role(vai_tro: str, ten: str, *, bat_buoc: bool) -> None:
        ten_chuan = ten.strip()
        if not ten_chuan:
            if bat_buoc:
                problems.append(f"vai tro {vai_tro} chua duoc cau hinh")
            return
        if ten_chuan not in LLM_REGISTRY:
            problems.append(
                f"LLM provider '{ten_chuan}' (vai tro {vai_tro}) khong ton tai trong registry"
            )

    _check_llm_role("tong hop (llm_primary_provider)", settings.llm_primary_provider, bat_buoc=True)
    _check_llm_role("xac minh (llm_verify_provider)", settings.llm_verify_provider, bat_buoc=True)
    # Blank normalization = uses the verification provider (R13.4) → not required.
    _check_llm_role("chuan hoa (llm_normalize_provider)", settings.llm_normalize_provider, bat_buoc=False)

    embedding = settings.embedding_provider.strip()
    if not embedding:
        problems.append("vai tro embedding (embedding_provider) chua duoc cau hinh")
    elif embedding not in EMBEDDING_REGISTRY:
        problems.append(
            f"Embedding provider '{embedding}' khong ton tai trong registry"
        )

    if problems:
        thong_diep = "Cau hinh provider khong hop le: " + "; ".join(problems)
        logger.error("Dung khoi tao — %s", thong_diep)
        raise InitializationError(
            thong_diep,
            details={
                "problems": problems,
                "llmProviders": sorted(LLM_REGISTRY),
                "embeddingProviders": sorted(EMBEDDING_REGISTRY),
            },
        )

    normalize = settings.llm_normalize_provider.strip() or "(=xac minh)"
    logger.info(
        "Cau hinh provider hop le: tong hop=%s, xac minh=%s, chuan hoa=%s, embedding=%s",
        settings.llm_primary_provider,
        settings.llm_verify_provider,
        normalize,
        settings.embedding_provider,
    )
