"""Where the files authored outside ``src/`` are, in either kind of install.

Three resources a running command needs are not part of the Python package,
because the package directory is not where a person edits or reads them:

* ``skill/`` -- the agent skill ``install-skill`` deploys.
* ``docs/note-writing-guideline.md`` -- the rules ``render --expand-prompt``
  quotes into its bundle.
* ``examples/overlay-minimal/`` -- the five overlay files ``profile init``
  writes into ``~/.lecture2notes/``.

v0.2.0 found them by walking up from this file to the repository root, which
works in a checkout and in ``pip install -e .`` and in nothing else. A plain
``pip install`` shipped none of them, so ``install-skill`` told every user who
had not cloned the repository to clone it.

The fix keeps a single editable copy of each file at its repository path and
maps it into ``lecture2notes/_bundled/`` at build time (see the
``force-include`` table in ``pyproject.toml``). This module is the other half:
it looks for the repository original **first** and falls back to the bundled
copy. That order matters. An editable install exposes ``src/`` directly and a
``_bundled/`` directory must never appear there; but if one ever did -- a stray
build artefact, a half-cleaned working tree -- preferring the repository
original means the checkout still reads the file the author is editing rather
than a frozen copy of it. There is exactly one source of truth and it is the
one in ``git``.

Everything here returns ``None`` rather than raising when a resource is
absent. The callers differ on what absence means: a missing ``skill/`` is a
hard error for ``install-skill``, while a missing guideline document only
means ``expand_prompt`` prints the path instead of the text.
"""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path
from typing import Optional

#: The directory the build maps the outside-the-package files into. It does
#: not exist in a source checkout and is not tracked; see the module docstring.
BUNDLE_DIRNAME = "_bundled"

#: Each resource as (repository-relative path, a file that proves it is the
#: real thing rather than an empty directory of the same name).
SKILL_RELATIVE = "skill"
SKILL_MARKER = "SKILL.md"

GUIDELINE_RELATIVE = "docs/note-writing-guideline.md"

OVERLAY_EXAMPLE_RELATIVE = "examples/overlay-minimal"
OVERLAY_EXAMPLE_MARKER = "outputs.toml"


def bundle_root() -> Optional[Path]:
    """``lecture2notes/_bundled`` inside an installed wheel, or ``None``.

    Resolved through :mod:`importlib.resources` rather than ``__file__`` so
    the lookup is the package's own, not this file's neighbourhood. A loader
    whose resources are not real files on disk -- a zipimport, say -- yields a
    traversable that has no filesystem path; that is not a case this project
    supports, and ``os.fspath`` raising ``TypeError`` is how it says so.
    """
    try:
        traversable = resources.files(__package__ or "lecture2notes")
    except (ImportError, ModuleNotFoundError, TypeError):  # pragma: no cover
        return None
    try:
        base = Path(os.fspath(traversable))
    except TypeError:  # pragma: no cover - namespace or zipped package
        return None
    candidate = base / BUNDLE_DIRNAME
    return candidate if candidate.is_dir() else None


def repo_root(start: Optional[Path] = None) -> Optional[Path]:
    """The checkout this package is being run from, or ``None``.

    Identified by the two files that only ever sit together at the repository
    root next to a ``src/`` tree. Walking up rather than counting ``parents``
    keeps the answer right whether the package is imported from ``src/`` via
    ``PYTHONPATH``, from an editable install, or from nowhere near a checkout.
    """
    here = Path(start) if start is not None else Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "pyproject.toml").is_file() and (parent / "src").is_dir():
            return parent
    return None


def _resolve(relative: str, marker: Optional[str] = None) -> Optional[Path]:
    """The repository copy of *relative* if there is one, else the bundled copy."""
    for base in (repo_root(), bundle_root()):
        if base is None:
            continue
        candidate = base.joinpath(*relative.split("/"))
        proof = candidate / marker if marker else candidate
        if marker:
            if proof.is_file():
                return candidate
        elif candidate.is_file():
            return candidate
    return None


def skill_dir() -> Optional[Path]:
    """The ``skill/`` directory ``install-skill`` copies from."""
    return _resolve(SKILL_RELATIVE, SKILL_MARKER)


def guideline_doc() -> Optional[Path]:
    """``docs/note-writing-guideline.md``, the document the rules live in."""
    return _resolve(GUIDELINE_RELATIVE)


def overlay_example_dir() -> Optional[Path]:
    """``examples/overlay-minimal/``, the folder ``profile init`` copies."""
    return _resolve(OVERLAY_EXAMPLE_RELATIVE, OVERLAY_EXAMPLE_MARKER)


def searched_paths(relative: str) -> str:
    """The places a lookup for *relative* looked, for an error message."""
    bases = [base for base in (repo_root(), bundle_root()) if base is not None]
    if not bases:
        return relative
    return ", ".join(str(base.joinpath(*relative.split("/"))) for base in bases)


__all__ = [
    "BUNDLE_DIRNAME",
    "GUIDELINE_RELATIVE",
    "OVERLAY_EXAMPLE_MARKER",
    "OVERLAY_EXAMPLE_RELATIVE",
    "SKILL_MARKER",
    "SKILL_RELATIVE",
    "bundle_root",
    "guideline_doc",
    "overlay_example_dir",
    "repo_root",
    "searched_paths",
    "skill_dir",
]
