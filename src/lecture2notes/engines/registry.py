"""Engine lookup for ``--engine`` and the ``--allow-cloud`` privacy gate.

New module for this package. The rad-workflow scripts hard-coded a two-value
``--engine`` choice inside argparse; a registry is what lets a fourth engine, or
a third-party one, be added without touching the CLI.

The gate is the point of the registry: any engine whose metadata says it is not
local sends audio to somebody else's machine, so it cannot run unless the user
asks for that in the same command.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from lecture2notes.engines.base import Engine, EngineMeta
from lecture2notes.engines.breeze_ct2 import BreezeCT2Engine
from lecture2notes.engines.faster_whisper import FasterWhisperEngine
from lecture2notes.engines.qwen3_asr import Qwen3AsrEngine
from lecture2notes.engines.whisper_cpp import WhisperCppEngine

#: The engine used when ``--engine`` is not given.
DEFAULT_ENGINE = "breeze_ct2"

CLOUD_GATE_MESSAGE = (
    "engine '%s' is not local: it would upload the audio to a third party. "
    "Re-run with --allow-cloud if that is what you want."
)


class CloudEngineBlocked(Exception):
    """Raised when a non-local engine is requested without --allow-cloud."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(CLOUD_GATE_MESSAGE % name)


_REGISTRY: Dict[str, Callable[..., Engine]] = {}


def register(factory: Callable[..., Engine], name: Optional[str] = None) -> None:
    """Add an engine class (or any factory carrying ``meta``) to the registry."""
    key = name or getattr(factory, "meta").name
    _REGISTRY[key] = factory


def unregister(name: str) -> None:
    """Remove an engine. Used by tests that register a fake backend."""
    _REGISTRY.pop(name, None)


def names() -> List[str]:
    return sorted(_REGISTRY)


def meta_for(name: str) -> EngineMeta:
    return getattr(_get(name), "meta")


def list_engines() -> List[EngineMeta]:
    """Metadata for every registered engine, in registration-name order."""
    return [meta_for(name) for name in names()]


def _get(name: str) -> Callable[..., Engine]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            "unknown engine: %s (known: %s)" % (name, ", ".join(names()))
        ) from None


def create(name: str, allow_cloud: bool = False, **options: Any) -> Engine:
    """Instantiate an engine, refusing a non-local one without ``allow_cloud``."""
    factory = _get(name)
    meta = getattr(factory, "meta")
    if not meta.local and not allow_cloud:
        raise CloudEngineBlocked(name)
    return factory(**options)


for _factory in (BreezeCT2Engine, FasterWhisperEngine, WhisperCppEngine, Qwen3AsrEngine):
    register(_factory)


__all__ = [
    "CLOUD_GATE_MESSAGE",
    "CloudEngineBlocked",
    "DEFAULT_ENGINE",
    "create",
    "list_engines",
    "meta_for",
    "names",
    "register",
    "unregister",
]
