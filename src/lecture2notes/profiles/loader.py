"""Read the effective configuration: cli > project > user > profile > builtin.

The mechanics of the layer stack live in :mod:`lecture2notes.profiles.layers`;
this module is the face the rest of the package calls. Every function here takes
the same ``profile`` argument it always took and returns the same shape it always
returned -- what changed is that the answer is now merged across the layers
instead of read out of one directory.

That distinction matters to callers in exactly one way: a value can now come
from a file the package does not own. So nothing here silently swallows a parse
error. A malformed overlay raises :class:`OverlayError`, which the CLI turns
into a path, a line number and exit 2.

The command-line layer is not threaded through every call signature, because
that would mean editing every stage to hand ``--profile`` along. Instead
:func:`cli_layer` installs it for the duration of one command, and the CLI entry
point is the only caller that opens that context.
"""

from __future__ import annotations

import contextlib
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

from lecture2notes.profiles.layers import (
    BUILTIN_PROFILE,
    CORRECTIONS_FILE,
    FRONTMATTER_TEMPLATE,
    LAYER_NAMES,
    META_PREFIX,
    NOTE_TEMPLATE,
    OUTPUTS_FILE,
    OVERLAY_DIR,
    OVERLAY_FILES,
    PRIVACY_FILE,
    PROFILES_DIR,
    TEMPLATE_FILES,
    OverlayError,
    builtin_profiles,
    flatten,
    layer_file,
    layer_stack,
    merge_tables,
    profile_dir,
    project_dir,
    read_json,
    read_template,
    read_toml,
    select_profile,
    user_dir,
)

#: What the generic profile falls back to when ``outputs.toml`` is absent.
DEFAULT_NOTE_STYLE = "concise"
#: R5 is a warning unless a profile promotes it.
DEFAULT_TRANSCRIPT_PASTE = "warning"
#: R10 (a section still mostly the render output) is a warning by
#: default: a skeleton is a legitimate intermediate state. A profile whose
#: notes get published can set `guideline.unexpanded = "error"`.
DEFAULT_UNEXPANDED = "warning"

#: How much of a section's body may still be the renderer's own sentences
#: before R10 calls it unexpanded. A profile moves the line with
#: ``check.r10_ratio`` in ``outputs.toml``; see :func:`r10_ratio`.
DEFAULT_R10_RATIO = 0.60

#: The ``outputs.toml`` key that names the profile, and so is reported as the
#: ``profile`` setting rather than as an output setting of its own.
PROFILE_KEY = "profile"

#: Dotted-key prefixes used when the mergeable files are listed together by
#: ``l2n profile show``.
PRIVACY_PREFIX = "privacy."
CORRECTIONS_PREFIX = "corrections."
TEMPLATE_PREFIX = "template."

# --------------------------------------------------------------------------
# the command-line layer
# --------------------------------------------------------------------------
_cli: Dict[str, Any] = {}


@contextlib.contextmanager
def cli_layer(profile: Optional[str] = None, **overrides: Any) -> Iterator[None]:
    """Install the command-line layer for the duration of one command.

    Scoped rather than global on purpose. A process that runs two commands --
    which is every in-process test of the CLI -- must not have the first one's
    ``--profile`` still in force during the second, and a unit test that calls
    :func:`note_style` directly must not inherit a flag from a CLI test that ran
    before it. ``None`` values are dropped, so passing an unset argument is the
    same as not passing it. A keyword's ``__`` becomes a dot, so
    ``note__style="faithful"`` sets the ``note.style`` key.
    """
    global _cli
    previous = _cli
    fresh: Dict[str, Any] = {}
    if profile:
        fresh[PROFILE_KEY] = str(profile)
    for key, value in overrides.items():
        if value is not None:
            fresh[key.replace("__", ".")] = value
    _cli = fresh
    try:
        yield
    finally:
        _cli = previous


def cli_overrides() -> Dict[str, Any]:
    """A copy of the command-line layer currently in force."""
    return dict(_cli)


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Setting:
    """One effective value and the layer it came from."""

    value: Any
    source: str

    def as_json(self) -> Dict[str, Any]:
        value = self.value
        if isinstance(value, Path):
            value = str(value)
        return {"value": value, "source": self.source}


@dataclass
class Resolved:
    """The whole effective configuration, with a source layer for every key."""

    profile: str
    profile_source: str
    outputs: Dict[str, Any] = field(default_factory=dict)
    privacy: Dict[str, Any] = field(default_factory=dict)
    corrections: Dict[str, Any] = field(default_factory=dict)
    templates: Dict[str, Optional[Path]] = field(default_factory=dict)
    sources: Dict[str, str] = field(default_factory=dict)
    layers: List[Tuple[str, Path]] = field(default_factory=list)

    # -- settings listing --------------------------------------------------
    def settings(self) -> "OrderedDict[str, Setting]":
        """Every effective key, in a stable reading order.

        Order is the profile, then the output settings, then privacy, then the
        two templates, then the correction pairs -- which come last because
        there are many of them and they are the least likely thing a person is
        checking when they run ``profile show``.
        """
        out: "OrderedDict[str, Setting]" = OrderedDict()
        out[PROFILE_KEY] = Setting(self.profile, self.profile_source)
        for key, value in flatten(self.outputs):
            if key == PROFILE_KEY:
                continue  # already reported above, with its own source layer
            out[key] = Setting(value, self.sources.get(key, "builtin"))
        for key, value in flatten(self.privacy, PRIVACY_PREFIX):
            out[key] = Setting(value, self.sources.get(key, "builtin"))
        for filename in TEMPLATE_FILES:
            path = self.templates.get(filename)
            if path is None:
                continue
            dotted = TEMPLATE_PREFIX + filename
            out[dotted] = Setting(path, self.sources.get(dotted, "builtin"))
        for key, value in flatten(self.corrections, CORRECTIONS_PREFIX):
            out[key] = Setting(value, self.sources.get(key, "builtin"))
        return out

    def as_json(self) -> "OrderedDict[str, Dict[str, Any]]":
        return OrderedDict(
            (key, setting.as_json()) for key, setting in self.settings().items()
        )

    # -- correction pairs --------------------------------------------------
    def correction_entries(self) -> List[Dict[str, Any]]:
        """``{heard, correct, section, source, layer}`` for every pair.

        ``source`` is the provenance label the table declares for its section in
        ``_sources``; it is what a correction record in the canonical JSON
        carries, and it is what the "nothing derived from a private vault" test
        reads. ``layer`` is a different thing and is kept separate: it says
        which of the five layers supplied the pair.
        """
        declared = self.corrections.get("_sources")
        labels = declared if isinstance(declared, Mapping) else {}
        entries: List[Dict[str, Any]] = []
        for section, table in self.corrections.items():
            if str(section).startswith(META_PREFIX) or not isinstance(table, Mapping):
                continue
            for heard, correct in table.items():
                dotted = "%s%s.%s" % (CORRECTIONS_PREFIX, section, heard)
                entries.append(
                    {
                        "heard": str(heard),
                        "correct": str(correct),
                        "section": str(section),
                        "source": str(labels.get(section, section)),
                        "layer": self.sources.get(dotted, "builtin"),
                    }
                )
        return entries


def resolve(
    profile: Optional[str] = None,
    cwd: Optional[Path] = None,
    home: Optional[Path] = None,
    overrides: Optional[Mapping[str, Any]] = None,
) -> Resolved:
    """Merge every layer into one effective configuration.

    ``profile`` is the caller's own choice (for example the ``profile`` field of
    a canonical JSON). The command-line layer still outranks it, which is the
    whole point of the ordering: ``--profile radiology`` has to win over a
    document that was rendered under generic.
    """
    cli = cli_overrides()
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                cli[key] = value

    chosen = cli.get(PROFILE_KEY) or None
    if chosen:
        name, source = str(chosen), "cli"
    else:
        name, source = select_profile(None, cwd=cwd, home=home)
        if source == "builtin" and profile:
            # The caller named one and no overlay overruled it.
            name = str(profile)
            source = "profile" if name != BUILTIN_PROFILE else "builtin"

    stack = layer_stack(name, cwd=cwd, home=home)
    result = Resolved(profile=name, profile_source=source, layers=stack)

    # Bottom layer first, so the last writer of a key is the winner.
    for layer_name, directory in reversed(stack):
        path = layer_file(directory, OUTPUTS_FILE)
        if path is not None:
            merge_tables(result.outputs, read_toml(path), result.sources, layer_name)
        path = layer_file(directory, PRIVACY_FILE)
        if path is not None:
            merge_tables(
                result.privacy,
                read_toml(path),
                result.sources,
                layer_name,
                prefix=PRIVACY_PREFIX,
            )
        path = layer_file(directory, CORRECTIONS_FILE)
        if path is not None:
            merge_tables(
                result.corrections,
                read_json(path),
                result.sources,
                layer_name,
                prefix=CORRECTIONS_PREFIX,
            )
        for filename in TEMPLATE_FILES:
            path = layer_file(directory, filename)
            if path is None:
                continue
            read_template(path, filename)  # parse now, so a broken file exits 2
            result.templates[filename] = path
            result.sources[TEMPLATE_PREFIX + filename] = layer_name

    for key, value in cli.items():
        if key == PROFILE_KEY:
            continue
        parts = key.split(".")
        table: Dict[str, Any] = result.outputs
        for part in parts[:-1]:
            nested = table.get(part)
            if not isinstance(nested, dict):
                nested = {}
                table[part] = nested
            table = nested
        table[parts[-1]] = value
        result.sources[key] = "cli"

    return result


# --------------------------------------------------------------------------
# the stable surface the rest of the package calls
# --------------------------------------------------------------------------
def read_file(filename: str, profile: str = BUILTIN_PROFILE) -> Optional[str]:
    """The winning layer's copy of one file, or None when no layer ships it."""
    for _layer_name, directory in resolve(profile).layers:
        path = layer_file(directory, filename)
        if path is None:
            continue
        if filename in TEMPLATE_FILES:
            return read_template(path, filename)
        return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    return None


def load_toml(filename: str, profile: str = BUILTIN_PROFILE) -> Dict[str, Any]:
    """The merged table for one TOML file; an absent file is an empty table."""
    if filename == OUTPUTS_FILE:
        return resolve(profile).outputs
    if filename == PRIVACY_FILE:
        return resolve(profile).privacy
    merged: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    for layer_name, directory in reversed(resolve(profile).layers):
        path = layer_file(directory, filename)
        if path is not None:
            merge_tables(merged, read_toml(path), sources, layer_name)
    return merged


def outputs(profile: str = BUILTIN_PROFILE) -> Dict[str, Any]:
    """The merged ``outputs.toml`` table (``pbf``, ``hub``, ``note.style``...)."""
    return resolve(profile).outputs


def privacy(profile: str = BUILTIN_PROFILE) -> Dict[str, Any]:
    return resolve(profile).privacy


def corrections(profile: str = BUILTIN_PROFILE) -> Dict[str, Any]:
    """The merged ``corrections.json`` document, section by section."""
    return resolve(profile).corrections


def correction_entries(profile: str = BUILTIN_PROFILE) -> List[Dict[str, Any]]:
    return resolve(profile).correction_entries()


def note_style(profile: str = BUILTIN_PROFILE, override: Optional[str] = None) -> str:
    """``--style`` wins; otherwise the effective ``note.style``; else concise."""
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


def unexpanded_severity(profile: str = BUILTIN_PROFILE) -> str:
    """Severity R10 reports at: ``warning`` by default, ``error`` if set."""
    table = outputs(profile).get("guideline")
    if isinstance(table, Mapping):
        value = table.get("unexpanded")
        if isinstance(value, str) and value:
            return value
    return DEFAULT_UNEXPANDED


def r10_ratio(profile: str = BUILTIN_PROFILE) -> float:
    """The R10 residual-ratio threshold: ``check.r10_ratio``, else 0.60.

    Lives under ``[check]`` rather than beside ``guideline.unexpanded``
    because it is a number the check computes, not a statement about how
    severe the guideline considers the finding. Values outside ``(0, 1]`` are
    ignored rather than clamped: a profile that writes ``r10_ratio = 60``
    means percent, and silently reading that as "report everything" would be
    worse than using the default.
    """
    table = outputs(profile).get("check")
    if isinstance(table, Mapping):
        value = table.get("r10_ratio")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if 0.0 < float(value) <= 1.0:
                return float(value)
    return DEFAULT_R10_RATIO


def privacy_patterns(profile: str = BUILTIN_PROFILE) -> List[str]:
    """Regular expressions a note line must not match (R7)."""
    raw = privacy(profile).get("patterns")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item]


def frontmatter_template(profile: str = BUILTIN_PROFILE) -> Optional[str]:
    return read_file(FRONTMATTER_TEMPLATE, profile)


def note_template(profile: str = BUILTIN_PROFILE) -> Optional[str]:
    return read_file(NOTE_TEMPLATE, profile)


__all__ = [
    "BUILTIN_PROFILE",
    "CORRECTIONS_FILE",
    "CORRECTIONS_PREFIX",
    "DEFAULT_NOTE_STYLE",
    "DEFAULT_TRANSCRIPT_PASTE",
    "DEFAULT_R10_RATIO",
    "DEFAULT_UNEXPANDED",
    "r10_ratio",
    "unexpanded_severity",
    "FRONTMATTER_TEMPLATE",
    "LAYER_NAMES",
    "NOTE_TEMPLATE",
    "OUTPUTS_FILE",
    "OVERLAY_DIR",
    "OVERLAY_FILES",
    "PRIVACY_FILE",
    "PRIVACY_PREFIX",
    "PROFILES_DIR",
    "PROFILE_KEY",
    "TEMPLATE_FILES",
    "TEMPLATE_PREFIX",
    "OverlayError",
    "Resolved",
    "Setting",
    "builtin_profiles",
    "cli_layer",
    "cli_overrides",
    "correction_entries",
    "corrections",
    "frontmatter_template",
    "load_toml",
    "note_style",
    "note_template",
    "outputs",
    "privacy",
    "privacy_patterns",
    "profile_dir",
    "project_dir",
    "read_file",
    "resolve",
    "transcript_paste_severity",
    "user_dir",
]
