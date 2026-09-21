"""Read the files of the built-in profile.

This is deliberately the smallest thing that works. The layered resolution the
design calls for -- ``cli > project > user > profile > builtin`` -- is a later
task group; until it lands, the note renderer and the note check still need
somewhere to get their templates and their settings from, and hard-coding those
into the renderer is exactly what the "frontmatter comes from templates, not
code" requirement forbids. So this module reads the built-in profile directory
and nothing else, and every caller takes the resolved values as arguments so the
layered resolver can be dropped in front of it without touching them.

A missing file is not an error here. A profile that does not ship a
``privacy.toml`` simply has no privacy patterns, and a caller that needs a
template can decide for itself whether an absent one is fatal.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

if sys.version_info >= (3, 11):  # pragma: no cover - one branch per interpreter
    import tomllib as _toml
else:  # pragma: no cover - one branch per interpreter
    import tomli as _toml

#: The profile shipped with the package and used when nothing else is chosen.
BUILTIN_PROFILE = "generic"

#: Filenames this loader knows about, so a typo fails at import rather than at
#: render time with an empty template.
FRONTMATTER_TEMPLATE = "note.frontmatter.yaml"
NOTE_TEMPLATE = "note.template.md"
OUTPUTS_FILE = "outputs.toml"
PRIVACY_FILE = "privacy.toml"

#: What the generic profile falls back to when ``outputs.toml`` is absent.
DEFAULT_NOTE_STYLE = "concise"
#: R5 is a warning unless a profile promotes it.
DEFAULT_TRANSCRIPT_PASTE = "warning"

PROFILES_DIR = Path(__file__).resolve().parent


def profile_dir(name: str = BUILTIN_PROFILE) -> Path:
    """The directory of a built-in profile. The path need not exist."""
    return PROFILES_DIR / name


def read_file(filename: str, profile: str = BUILTIN_PROFILE) -> Optional[str]:
    """One profile file as text, or None when the profile does not ship it."""
    path = profile_dir(profile) / filename
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def load_toml(filename: str, profile: str = BUILTIN_PROFILE) -> Dict[str, Any]:
    """Parse one TOML file of a profile; an absent file is an empty table."""
    path = profile_dir(profile) / filename
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return _toml.load(handle)


def outputs(profile: str = BUILTIN_PROFILE) -> Dict[str, Any]:
    return load_toml(OUTPUTS_FILE, profile)


def note_style(profile: str = BUILTIN_PROFILE, override: Optional[str] = None) -> str:
    """``--style`` wins; otherwise the profile's ``note.style``; else concise."""
    if override:
        return override
    table = outputs(profile).get("note")
    if isinstance(table, Mapping):
        value = table.get("style")
        if isinstance(value, str) and value:
            return value
    return DEFAULT_NOTE_STYLE


def transcript_paste_severity(profile: str = BUILTIN_PROFILE) -> str:
    """Severity R5 reports at: ``warning`` by default, ``error`` if set."""
    table = outputs(profile).get("guideline")
    if isinstance(table, Mapping):
        value = table.get("transcript_paste")
        if isinstance(value, str) and value:
            return value
    return DEFAULT_TRANSCRIPT_PASTE


def privacy_patterns(profile: str = BUILTIN_PROFILE) -> List[str]:
    """Regular expressions a note line must not match (R7)."""
    table = privacy(profile)
    raw = table.get("patterns")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item]


def privacy(profile: str = BUILTIN_PROFILE) -> Dict[str, Any]:
    return load_toml(PRIVACY_FILE, profile)


def frontmatter_template(profile: str = BUILTIN_PROFILE) -> Optional[str]:
    return read_file(FRONTMATTER_TEMPLATE, profile)


def note_template(profile: str = BUILTIN_PROFILE) -> Optional[str]:
    return read_file(NOTE_TEMPLATE, profile)


__all__ = [
    "BUILTIN_PROFILE",
    "DEFAULT_NOTE_STYLE",
    "DEFAULT_TRANSCRIPT_PASTE",
    "FRONTMATTER_TEMPLATE",
    "NOTE_TEMPLATE",
    "OUTPUTS_FILE",
    "PRIVACY_FILE",
    "PROFILES_DIR",
    "frontmatter_template",
    "load_toml",
    "note_style",
    "note_template",
    "outputs",
    "privacy",
    "privacy_patterns",
    "profile_dir",
    "read_file",
    "transcript_paste_severity",
]
