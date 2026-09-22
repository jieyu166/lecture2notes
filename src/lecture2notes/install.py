"""Deploy the packaged ``skill/`` directory to the three agent skill folders.

Three decisions here are load-bearing and each has a failure mode that is
silent if it goes the other way:

* **Copy, never symlink.** A symlink makes the target track the repo, which
  looks convenient until the repo moves or the agent host refuses to follow
  links, and then the skill is simply gone with no error anywhere.
* **Never touch an overlay file that is already in the target.** The five
  overlay filenames are the user's own configuration. Re-installing a skill
  update must not be a way to lose them, so they are skipped even if a future
  ``skill/`` ships files with those names.
* **``--check`` recomputes the target's hash from the target's own files**
  rather than reading ``.installed.json``. A drift check that trusts a record
  written at install time reports "ok" for a directory someone has edited,
  which is the one case the check exists for.

Content hashes normalise CRLF to LF before hashing: the repo checks out with
LF, but a target directory that has been through an editor on Windows may not,
and a newline difference is not drift worth reporting.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from lecture2notes import __version__, resources

#: The skill's name, and therefore the leaf directory name in every target.
SKILL_NAME = "lecture2notes"

#: Where each agent looks for its skills, relative to the home directory.
TARGET_RELATIVE_PATHS: Dict[str, Tuple[str, ...]] = {
    "claude": (".claude", "skills", SKILL_NAME),
    "codex": (".agents", "skills", SKILL_NAME),
    "opencode": (".config", "opencode", "skills", SKILL_NAME),
}

#: Deployment order, so ``--all`` prints the same three lines every run.
TARGET_NAMES: Tuple[str, ...] = ("claude", "codex", "opencode")

#: User-owned files an install must never overwrite or delete. Kept in sync
#: with ``profiles/loader.py`` plus the corrections table, which the loader
#: does not name because it merges rather than replaces it.
OVERLAY_FILENAMES: Tuple[str, ...] = (
    "note.frontmatter.yaml",
    "note.template.md",
    "corrections.json",
    "outputs.toml",
    "privacy.toml",
)

#: The install record. Not an input to ``--check``; see the module docstring.
RECORD_NAME = ".installed.json"


class InstallError(RuntimeError):
    """The source skill directory is missing, or no target was selected."""


# --------------------------------------------------------------------------
# locating things
# --------------------------------------------------------------------------
def source_dir() -> Path:
    """The ``skill/`` directory to copy from.

    In a checkout that is the repository's own ``skill/``, the one a person
    edits; in an installed wheel it is the copy the build mapped into
    ``lecture2notes/_bundled/skill``. :mod:`lecture2notes.resources` decides
    which, preferring the repository original. Both kinds of install work,
    which is the whole of the 0.2.1 change; a directory that is neither still
    raises rather than installing nothing.
    """
    found = resources.skill_dir()
    if found is not None:
        return found
    raise InstallError(
        "skill source directory not found (looked for %s); reinstall the "
        "package, or install the repository with `pip install -e .`, to use "
        "install-skill" % resources.searched_paths(resources.SKILL_RELATIVE)
    )


def target_dir(name: str, home: Optional[Path] = None) -> Path:
    """Where ``--target <name>`` installs to, under *home* (default: real home)."""
    try:
        parts = TARGET_RELATIVE_PATHS[name]
    except KeyError:
        raise InstallError("unknown target %r" % name) from None
    base = Path(home) if home is not None else Path.home()
    return base.joinpath(*parts)


def resolve_targets(
    target: Optional[str] = None,
    all_targets: bool = False,
    dest: Optional[str] = None,
    home: Optional[Path] = None,
) -> List[Tuple[str, Path]]:
    """Turn the CLI flags into ``[(label, path), ...]``.

    ``--dest`` overrides the directory of a single target; combining it with
    ``--all`` is refused rather than silently installing three copies into one
    directory.
    """
    if all_targets and dest:
        raise InstallError("--dest cannot be combined with --all")
    if all_targets:
        return [(name, target_dir(name, home)) for name in TARGET_NAMES]
    if dest:
        return [(target or "custom", Path(dest))]
    if target:
        return [(target, target_dir(target, home))]
    raise InstallError(
        "choose a target: --target claude|codex|opencode, --all, or --dest"
    )


# --------------------------------------------------------------------------
# hashing
# --------------------------------------------------------------------------
def _normalised_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def iter_source_files(src: Optional[Path] = None) -> List[str]:
    """Relative POSIX paths of every file the skill ships, sorted."""
    root = src if src is not None else source_dir()
    names = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.name != RECORD_NAME
    ]
    return sorted(names)


def file_hash(path: Path) -> str:
    return hashlib.sha256(_normalised_bytes(path)).hexdigest()


def content_hash(root: Path, names: Sequence[str]) -> str:
    """sha256 over the concatenated contents of *names*, in the given order.

    A name with no file on disk contributes nothing, so a target missing a
    file hashes differently from one that has it -- which is what makes the
    aggregate comparison catch a deletion as well as an edit.
    """
    digest = hashlib.sha256()
    for name in names:
        path = Path(root) / name
        if path.is_file():
            digest.update(_normalised_bytes(path))
    return digest.hexdigest()


def source_hash(src: Optional[Path] = None) -> str:
    root = src if src is not None else source_dir()
    return content_hash(root, iter_source_files(root))


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------
def install_one(
    dest: Path, label: str, src: Optional[Path] = None
) -> Tuple[Path, List[str]]:
    """Copy the skill into *dest*. Returns the path and the files skipped."""
    root = src if src is not None else source_dir()
    names = iter_source_files(root)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    skipped: List[str] = []
    for name in names:
        target_path = dest / name
        if Path(name).name in OVERLAY_FILENAMES and target_path.exists():
            skipped.append(name)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target_path)

    record = {
        "version": __version__,
        "source_sha256": content_hash(root, names),
        "installed_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "target": label,
    }
    (dest / RECORD_NAME).write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return dest, skipped


def install(
    target: Optional[str] = None,
    all_targets: bool = False,
    dest: Optional[str] = None,
    home: Optional[Path] = None,
    src: Optional[Path] = None,
) -> List[Tuple[str, Path, List[str]]]:
    """Install to every selected target. Returns ``[(label, path, skipped)]``."""
    results = []
    for label, path in resolve_targets(target, all_targets, dest, home):
        installed, skipped = install_one(path, label, src)
        results.append((label, installed, skipped))
    return results


# --------------------------------------------------------------------------
# drift check
# --------------------------------------------------------------------------
def check_one(dest: Path, src: Optional[Path] = None) -> Tuple[bool, List[str]]:
    """``(matches, detail_lines)`` for one target, recomputed from disk."""
    root = src if src is not None else source_dir()
    names = iter_source_files(root)
    dest = Path(dest)

    if not dest.is_dir() or not (dest / "SKILL.md").is_file():
        return False, ["not installed"]

    if content_hash(root, names) == content_hash(dest, names):
        return True, []

    details: List[str] = []
    for name in names:
        installed = dest / name
        if not installed.is_file():
            details.append("%s: missing" % name)
        elif file_hash(installed) != file_hash(root / name):
            details.append("%s: differs" % name)
    if not details:
        # The aggregate differs but no listed file does: a file was removed
        # from the packaged skill and is still sitting in the target.
        details.append("directory content differs from the packaged skill")
    return False, details


def check(
    target: Optional[str] = None,
    all_targets: bool = False,
    dest: Optional[str] = None,
    home: Optional[Path] = None,
    src: Optional[Path] = None,
) -> Tuple[List[str], bool]:
    """``(lines, all_ok)``. *lines* is exactly what the CLI prints."""
    if not (target or all_targets or dest):
        all_targets = True
    lines: List[str] = []
    all_ok = True
    for _label, path in resolve_targets(target, all_targets, dest, home):
        matches, details = check_one(path, src)
        if matches:
            lines.append("[ok] %s" % path)
            continue
        all_ok = False
        if details == ["not installed"]:
            lines.append("[drift] %s: not installed" % path)
            continue
        lines.append("[drift] %s" % path)
        lines.extend("  %s" % detail for detail in details)
    return lines, all_ok


__all__ = [
    "InstallError",
    "OVERLAY_FILENAMES",
    "RECORD_NAME",
    "SKILL_NAME",
    "TARGET_NAMES",
    "TARGET_RELATIVE_PATHS",
    "check",
    "check_one",
    "content_hash",
    "file_hash",
    "install",
    "install_one",
    "iter_source_files",
    "resolve_targets",
    "source_dir",
    "source_hash",
    "target_dir",
]
