"""Engine lookup for ``--engine`` and the ``--allow-cloud`` privacy gate.

New module for this package. The rad-workflow scripts hard-coded a two-value
``--engine`` choice inside argparse; a registry is what lets a fourth engine, or
a third-party one, be added without touching the CLI.

The gate is the point of the registry: any engine whose metadata says it is not
local sends audio to somebody else's machine, so it cannot run unless the user
asks for that in the same command.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from lecture2notes.engines.base import DependencyStatus, Engine, EngineMeta
from lecture2notes.engines.breeze_ct2 import BreezeCT2Engine
from lecture2notes.engines.faster_whisper import FasterWhisperEngine
from lecture2notes.engines.qwen3_asr import Qwen3AsrEngine
from lecture2notes.engines.whisper_cpp import WhisperCppEngine

#: The engine used when ``--engine`` is not given.
DEFAULT_ENGINE = "breeze_ct2"

#: Printed verbatim when the gate fires. It names the rule, not just the flag,
#: because the flag is easy to add without understanding what it turns off.
CLOUD_GATE_MESSAGE = (
    "engine '%s' is not local: it would upload your audio to a third party. "
    "Non-local engines require --allow-cloud because of the local-processing "
    "privacy rule. Re-run with --allow-cloud only if you accept that."
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


# -- dependency probing ----------------------------------------------------

#: Column layout of ``--list-engines``, ASCII only so a cp950 console can print it.
LIST_HEADER = "engine          local  gpu    native_ts  dependencies"
_LIST_ROW = "%-15s %-6s %-6s %-10s %s"


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def probe(name: str, **options: Any) -> DependencyStatus:
    """Dependency state of one engine.

    The gate is deliberately not applied here: a user is allowed to see that a
    cloud plugin exists and whether it is installed without consenting to run it.
    """
    factory = _get(name)
    try:
        engine = factory(**options)
    except Exception as exc:  # a plugin whose constructor needs options we lack
        return DependencyStatus(name, False, "cannot construct: %s" % exc)
    return engine.probe()


def status_table() -> List[Tuple[EngineMeta, DependencyStatus]]:
    """Metadata plus dependency state for every registered engine."""
    return [(meta_for(name), probe(name)) for name in names()]


def engine_lines() -> List[str]:
    """The exact lines ``l2n transcribe --list-engines`` prints."""
    lines = [LIST_HEADER]
    for meta, status in status_table():
        detail = status.detail or ("ready" if status.satisfied else "missing")
        if not status.satisfied:
            detail = "missing: %s" % detail
        lines.append(
            _LIST_ROW
            % (
                meta.name,
                _yes(meta.local),
                _yes(meta.needs_gpu),
                _yes(meta.native_timestamps),
                detail,
            )
        )
    return lines


for _factory in (BreezeCT2Engine, FasterWhisperEngine, WhisperCppEngine, Qwen3AsrEngine):
    register(_factory)


__all__ = [
    "CLOUD_GATE_MESSAGE",
    "CloudEngineBlocked",
    "DEFAULT_ENGINE",
    "LIST_HEADER",
    "create",
    "engine_lines",
    "list_engines",
    "meta_for",
    "names",
    "probe",
    "register",
    "status_table",
    "unregister",
]
