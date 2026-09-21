"""Layered configuration resolution: cli > project > user > profile > builtin.

Five layers, highest precedence first:

``cli``
    Values named on the command line (``--profile``, ``--style``).
``project``
    ``./.lecture2notes/`` -- the overlay that travels with one course folder.
``user``
    ``~/.lecture2notes/`` -- the overlay that travels with one person. Home is
    read through :func:`pathlib.Path.home` at call time, never cached, so a test
    that repoints ``HOME``/``USERPROFILE`` gets the repointed directory.
``profile``
    ``profiles/<name>/`` inside the package, chosen by ``--profile`` or by the
    ``profile`` key of a higher layer.
``builtin``
    ``profiles/generic/`` -- the package defaults, always the bottom layer.

Exactly five files can be overlaid, and they do not merge the same way:

- ``note.frontmatter.yaml`` and ``note.template.md`` replace the lower layers
  whole. Half a template merged out of two layers is not a template; it is a
  document with someone else's headings in it.
- ``outputs.toml``, ``privacy.toml`` and ``corrections.json`` merge key by key
  with the higher layer winning, so an overlay that wants to change one setting
  does not have to restate the other four.

A layer that does not ship a given file contributes nothing for that file --
absence is never "set this back to the default".

A file that is present but malformed is the opposite of absence: it raises
:class:`OverlayError` and the command exits 2 without applying any part of it.
Silently skipping a broken overlay would hand back the default values under the
user's own settings' name, which is the failure mode this project treats as
worse than a crash.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Tuple

if sys.version_info >= (3, 11):  # pragma: no cover - one branch per interpreter
    import tomllib as _toml
else:  # pragma: no cover - one branch per interpreter
    import tomli as _toml

#: The profile shipped with the package and used when nothing else is chosen.
BUILTIN_PROFILE = "generic"

#: Layer names, highest precedence first. ``l2n profile show`` prints one of
#: these words next to every value.
LAYER_NAMES: Tuple[str, ...] = ("cli", "project", "user", "profile", "builtin")

#: The directory name both the project and the user overlay use.
OVERLAY_DIR = ".lecture2notes"

FRONTMATTER_TEMPLATE = "note.frontmatter.yaml"
NOTE_TEMPLATE = "note.template.md"
CORRECTIONS_FILE = "corrections.json"
OUTPUTS_FILE = "outputs.toml"
PRIVACY_FILE = "privacy.toml"

#: Exactly the files an overlay may contain. Anything else in an overlay
#: directory is ignored rather than guessed at.
OVERLAY_FILES: Tuple[str, ...] = (
    FRONTMATTER_TEMPLATE,
    NOTE_TEMPLATE,
    CORRECTIONS_FILE,
    OUTPUTS_FILE,
    PRIVACY_FILE,
)

#: Templates replace; tables merge.
TEMPLATE_FILES: Tuple[str, ...] = (FRONTMATTER_TEMPLATE, NOTE_TEMPLATE)

PROFILES_DIR = Path(__file__).resolve().parent

#: Keys beginning with this are documentation inside a data file, not settings.
META_PREFIX = "_"

_AT_LINE_RE = re.compile(r"at line (\d+)")


class OverlayError(Exception):
    """A configuration file exists but cannot be parsed.

    Carries the path and, whenever the parser gives one, the line number, so
    the CLI can print something a person can act on rather than a traceback.
    """

    def __init__(self, path: Path, detail: str, line: Optional[int] = None) -> None:
        self.path = Path(path)
        self.detail = detail
        self.line = line
        super().__init__(self.message())

    def message(self) -> str:
        where = "line %d" % self.line if self.line else "line ?"
        return "%s: %s: %s" % (self.path, where, self.detail)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
def _toml_line(exc: Exception) -> Optional[int]:
    """The line a TOML parse error happened on.

    ``tomllib`` on 3.14 exposes ``lineno``; every older interpreter and the
    ``tomli`` backport only put ``(at line N, column M)`` in the message, so
    both are read and neither is required.
    """
    lineno = getattr(exc, "lineno", None)
    if isinstance(lineno, int) and lineno > 0:
        return lineno
    match = _AT_LINE_RE.search(str(exc))
    if match:
        return int(match.group(1))
    return None


def read_toml(path: Path) -> Dict[str, Any]:
    """Parse one TOML file, or raise :class:`OverlayError` naming its line."""
    p = Path(path)
    try:
        with p.open("rb") as handle:
            data = _toml.load(handle)
    except OSError as exc:  # pragma: no cover - unreadable file
        raise OverlayError(p, "cannot be read (%s)" % exc) from exc
    except Exception as exc:  # tomllib / tomli raise TOMLDecodeError
        raise OverlayError(p, str(exc), _toml_line(exc)) from exc
    if not isinstance(data, dict):  # pragma: no cover - tomllib always maps
        raise OverlayError(p, "top level must be a table", 1)
    return data


def read_json(path: Path) -> Dict[str, Any]:
    """Parse one JSON file, or raise :class:`OverlayError` naming its line."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8-sig")
    except OSError as exc:  # pragma: no cover - unreadable file
        raise OverlayError(p, "cannot be read (%s)" % exc) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OverlayError(p, exc.msg, exc.lineno) from exc
    if not isinstance(data, dict):
        raise OverlayError(p, "top level must be a JSON object", 1)
    return data


def check_frontmatter_template(text: str, path: Path) -> None:
    """Validate a frontmatter template, which is flat ``key: value`` lines.

    Deliberately not PyYAML. The template's whole job is to carry
    ``{{placeholder}}`` markers, and a real YAML parser rejects the shipped
    generic template outright: ``date: {{date}}`` and ``tags: [{{tags}}]`` are
    flow mappings to YAML, so ``yaml.safe_load`` raises ``ConstructorError`` on
    the package's own default file. A validator that cannot accept the format
    it validates is worse than none. Frontmatter is flat key-value by
    construction, so this checks exactly that and reports the offending line.
    """
    fences = 0
    closed = False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("---") and set(stripped) <= set("-"):
            fences += 1
            if fences == 2:
                closed = True
            elif fences > 2:
                raise OverlayError(
                    path, "a frontmatter template has at most two '---' fences", number
                )
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if closed:
            raise OverlayError(path, "content after the closing '---' fence", number)
        if fences == 0:
            # Free text before the opening fence means the fences are missing;
            # a comment block is fine and was skipped above.
            raise OverlayError(
                path,
                "expected a comment or the opening '---' fence before any field",
                number,
            )
        if line[:1] in (" ", "\t"):
            raise OverlayError(
                path,
                "nested keys are not supported; a frontmatter template is flat "
                "'key: value' lines",
                number,
            )
        if ":" not in line:
            raise OverlayError(path, "expected 'key: value'", number)
        if not line.split(":", 1)[0].strip():
            raise OverlayError(path, "empty field name", number)
    if fences == 1:
        raise OverlayError(path, "the '---' fence is never closed", 1)


def read_template(path: Path, filename: str) -> str:
    """Read a template file, newline-normalised, validating YAML shape."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    except OSError as exc:  # pragma: no cover - unreadable file
        raise OverlayError(p, "cannot be read (%s)" % exc) from exc
    if filename == FRONTMATTER_TEMPLATE:
        check_frontmatter_template(text, p)
    return text


# --------------------------------------------------------------------------
# layer stack
# --------------------------------------------------------------------------
def overlay_dir(base: Path) -> Path:
    return Path(base) / OVERLAY_DIR


def project_dir(cwd: Optional[Path] = None) -> Path:
    return overlay_dir(Path(cwd) if cwd is not None else Path.cwd())


def user_dir(home: Optional[Path] = None) -> Path:
    return overlay_dir(Path(home) if home is not None else Path.home())


def profile_dir(name: str = BUILTIN_PROFILE) -> Path:
    """The directory of a built-in profile. The path need not exist."""
    return PROFILES_DIR / name


def builtin_profiles() -> List[str]:
    return sorted(
        p.name
        for p in PROFILES_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(("_", "."))
    )


def layer_stack(
    profile: str = BUILTIN_PROFILE,
    cwd: Optional[Path] = None,
    home: Optional[Path] = None,
) -> List[Tuple[str, Path]]:
    """The directories of every layer, highest precedence first.

    ``cli`` has no directory and so is not listed. The ``profile`` layer is
    omitted when the selected profile *is* the built-in one, because a layer
    that is literally the same directory as the one below it would report the
    wrong source word for every key it carries.
    """
    stack: List[Tuple[str, Path]] = [
        ("project", project_dir(cwd)),
        ("user", user_dir(home)),
    ]
    builtin = profile_dir(BUILTIN_PROFILE)
    chosen = profile_dir(profile or BUILTIN_PROFILE)
    if chosen != builtin:
        stack.append(("profile", chosen))
    stack.append(("builtin", builtin))
    return stack


def layer_file(directory: Path, filename: str) -> Optional[Path]:
    path = Path(directory) / filename
    return path if path.is_file() else None


def select_profile(
    cli: Optional[str] = None,
    cwd: Optional[Path] = None,
    home: Optional[Path] = None,
) -> Tuple[str, str]:
    """``(name, source layer)`` for the profile itself.

    ``--profile`` wins; then a top-level ``profile`` key in the project
    overlay's ``outputs.toml``; then the user overlay's; otherwise the built-in
    generic profile. The profile layer cannot name the profile -- that would be
    a file choosing which file to read.
    """
    if cli:
        return str(cli), "cli"
    for name, directory in (("project", project_dir(cwd)), ("user", user_dir(home))):
        path = layer_file(directory, OUTPUTS_FILE)
        if path is None:
            continue
        value = read_toml(path).get("profile")
        if isinstance(value, str) and value.strip():
            return value.strip(), name
    return BUILTIN_PROFILE, "builtin"


# --------------------------------------------------------------------------
# merging
# --------------------------------------------------------------------------
def merge_tables(
    base: MutableMapping[str, Any],
    incoming: Mapping[str, Any],
    sources: MutableMapping[str, str],
    layer: str,
    prefix: str = "",
) -> None:
    """Merge ``incoming`` over ``base`` key by key, recording each key's layer.

    Called from the bottom layer upwards, so the last writer of a key is the
    highest-precedence layer that set it and ``sources`` ends up holding the
    winner. Nested tables merge; every other value (including a list) is
    replaced whole, because half of one layer's list plus half of another's is
    a list neither layer asked for.
    """
    for key, value in incoming.items():
        dotted = "%s%s" % (prefix, key)
        existing = base.get(key)
        if isinstance(value, Mapping) and isinstance(existing, MutableMapping):
            merge_tables(existing, value, sources, layer, prefix="%s." % dotted)
            continue
        if isinstance(value, Mapping):
            fresh: Dict[str, Any] = {}
            merge_tables(fresh, value, sources, layer, prefix="%s." % dotted)
            base[key] = fresh
            sources[dotted] = layer
            continue
        base[key] = value
        sources[dotted] = layer


def flatten(table: Mapping[str, Any], prefix: str = "") -> List[Tuple[str, Any]]:
    """Dotted leaf keys of a table, in declaration order, metadata skipped."""
    items: List[Tuple[str, Any]] = []
    for key, value in table.items():
        if str(key).startswith(META_PREFIX):
            continue
        dotted = "%s%s" % (prefix, key)
        if isinstance(value, Mapping):
            items.extend(flatten(value, "%s." % dotted))
        else:
            items.append((dotted, value))
    return items


__all__ = [
    "BUILTIN_PROFILE",
    "CORRECTIONS_FILE",
    "FRONTMATTER_TEMPLATE",
    "LAYER_NAMES",
    "META_PREFIX",
    "NOTE_TEMPLATE",
    "OUTPUTS_FILE",
    "OVERLAY_DIR",
    "OVERLAY_FILES",
    "PRIVACY_FILE",
    "PROFILES_DIR",
    "TEMPLATE_FILES",
    "OverlayError",
    "builtin_profiles",
    "check_frontmatter_template",
    "flatten",
    "layer_file",
    "layer_stack",
    "merge_tables",
    "overlay_dir",
    "profile_dir",
    "project_dir",
    "read_json",
    "read_template",
    "read_toml",
    "select_profile",
    "user_dir",
]
