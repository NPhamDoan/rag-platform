"""Self-registering registry for ChienLuocChunk (R17).

Mirrors the LLM_Provider registry mechanism (`app/providers/registry.py`):
- Each strategy registers itself with the `@register_chunker("ten")` decorator.
- At startup, `discover_chunkers()` automatically imports EVERY `*_chunker.py` file in
  this package → the decorators run → the registry is populated (R17.2).
- Lookups go through `get_chunker(ten)`.

=> ADDING A NEW STRATEGY = create a `*_chunker.py` file + decorator, do NOT edit the
lookup function or the Document_Pipeline core (R17.2, R18.9).

When configuration specifies a strategy that does not exist, `get_chunker` raises
`ValidationError` for a clearly invalid strategy name (R17.7) — preserving the TaiLieu
state / not writing chunks is handled by the calling layer (Document_Pipeline).
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Callable

from app.errors import ValidationError

logger = logging.getLogger(__name__)

# Registry: strategy name (config) -> self-registered strategy class.
CHUNKER_REGISTRY: dict[str, type] = {}

_CHUNKER_SUFFIX = "_chunker"


def register_chunker(ten: str) -> Callable[[type], type]:
    """Decorator that registers a ChienLuocChunk under `ten` (used in configuration)."""
    ten_chuan = ten.strip()
    if not ten_chuan:
        raise ValueError("Ten chien luoc chunk khi dang ky khong duoc de trong")

    def decorator(cls: type) -> type:
        existing = CHUNKER_REGISTRY.get(ten_chuan)
        if existing is not None and existing is not cls:
            logger.warning(
                "Ghi de dang ky chien luoc chunk '%s': %s -> %s",
                ten_chuan,
                existing.__name__,
                cls.__name__,
            )
        CHUNKER_REGISTRY[ten_chuan] = cls
        logger.debug("Dang ky chien luoc chunk '%s' -> %s", ten_chuan, cls.__name__)
        return cls

    return decorator


def discover_chunkers() -> None:
    """Automatically discover + load every `*_chunker.py` file in this package (R17.2)."""
    package = importlib.import_module(__package__)
    discovered: list[str] = []
    for module_info in pkgutil.iter_modules(package.__path__):
        name = module_info.name
        if name.startswith("_") or name in {"registry", "auto_selector"}:
            continue
        if not name.endswith(_CHUNKER_SUFFIX):
            continue
        importlib.import_module(f"{__package__}.{name}")
        discovered.append(name)
    logger.info(
        "Nap registry chunker: %d module (%s); chien luoc=%s",
        len(discovered),
        ", ".join(sorted(discovered)) or "(khong co)",
        sorted(CHUNKER_REGISTRY),
    )


def get_chunker(ten: str) -> type:
    """Look up a ChienLuocChunk class by `ten`; if absent → ValidationError for a clear name (R17.7)."""
    cls = CHUNKER_REGISTRY.get(ten.strip())
    if cls is None:
        raise ValidationError(
            f"Chien luoc chunk '{ten}' khong ton tai trong registry",
            details={"available": sorted(CHUNKER_REGISTRY)},
        )
    return cls
