"""Write the shipped overlay example into a user's overlay directory.

``examples/overlay-minimal/`` is the answer to "what does an overlay look
like", and until 0.2.1 the README's answer to "how do I get one" was a ``cp``
from a repository clone. Someone who installed the package from PyPI had no
clone and therefore no example, which made the overlay feature documented and
unreachable at the same time. ``l2n profile init`` writes the same five files
from the copy that now ships inside the package.

One rule governs the whole module: **never overwrite**. These five filenames
are the user's own configuration, and a bootstrap command that clobbers them
is a command nobody can safely run twice. A file already in the destination is
reported as kept and left byte-for-byte alone, so ``profile init`` after an
upgrade adds whatever is missing and touches nothing else. That is the same
promise ``install-skill`` makes about the same five names.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional, Tuple

from lecture2notes import resources
from lecture2notes.profiles.layers import OVERLAY_DIR, OVERLAY_FILES

#: Copied alongside the five overlay files. It is what explains the `消化層級`
#: field and the 0-3 scale, and a directory of five example files with no
#: explanation of why they say what they say is half an example.
EXAMPLE_README = "README.md"


class BootstrapError(RuntimeError):
    """The packaged overlay example could not be found."""


def example_dir() -> Path:
    """The packaged ``examples/overlay-minimal/``, or raise."""
    found = resources.overlay_example_dir()
    if found is not None:
        return found
    raise BootstrapError(
        "overlay example not found (looked for %s); reinstall the package to "
        "use profile init"
        % resources.searched_paths(resources.OVERLAY_EXAMPLE_RELATIVE)
    )


def example_files(src: Optional[Path] = None) -> List[str]:
    """The filenames ``profile init`` writes, in a stable order."""
    root = Path(src) if src is not None else example_dir()
    names = list(OVERLAY_FILES)
    if (root / EXAMPLE_README).is_file():
        names.append(EXAMPLE_README)
    return names


def default_dest(home: Optional[Path] = None) -> Path:
    """``~/.lecture2notes``, the layer the README tells people to fill."""
    base = Path(home) if home is not None else Path.home()
    return base / OVERLAY_DIR


def init_overlay(
    dest: Optional[str] = None,
    home: Optional[Path] = None,
    src: Optional[Path] = None,
) -> Tuple[Path, List[str], List[str]]:
    """Write the example into *dest*. Returns ``(dest, written, kept)``."""
    root = Path(src) if src is not None else example_dir()
    target = Path(dest) if dest else default_dest(home)
    target.mkdir(parents=True, exist_ok=True)

    written: List[str] = []
    kept: List[str] = []
    for name in example_files(root):
        source = root / name
        if not source.is_file():  # pragma: no cover - guarded by example_files
            continue
        if (target / name).exists():
            kept.append(name)
            continue
        shutil.copyfile(source, target / name)
        written.append(name)
    return target, written, kept


__all__ = [
    "EXAMPLE_README",
    "BootstrapError",
    "default_dest",
    "example_dir",
    "example_files",
    "init_overlay",
]
