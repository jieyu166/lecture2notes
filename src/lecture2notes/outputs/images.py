"""Collect the images a note actually embeds, so the note can be moved alone.

Ported from rad-workflow skills/lecture-to-notes/scripts/collect_note_images.py.

A finished note embeds a small fraction of the captured frames: a dozen out of a
hundred and fifty is typical. Moving the note into a vault means moving those
twelve, not the whole capture directory, so the embeds are parsed out of the
Markdown and only the referenced files are copied.

Both embed spellings are understood: the Markdown form, whose path can be
percent-encoded, and the wikilink form, which carries only a bare filename.
"""

from __future__ import annotations

import os
import re
import shutil
import urllib.parse
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
EMBED = re.compile(r"!\[[^\]]*\]\(([^)]+?)\)|!\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]")
#: Where a bare wikilink filename is looked for, in order.
SEARCH_SUBDIRS = ("", "frames", "images")


def referenced_images(markdown: str) -> List[str]:
    """Image basenames embedded in the note, de-duplicated, in document order."""
    out: List[str] = []
    for match in EMBED.finditer(markdown):
        raw = match.group(1) or match.group(2) or ""
        raw = urllib.parse.unquote(raw.strip().split(" ")[0].strip("<>"))
        if "://" in raw:
            continue
        if raw.lower().endswith(IMG_EXT):
            out.append(os.path.basename(raw))
    return list(dict.fromkeys(out))


def resolve(name: str, base_dir: Path, extra_dirs: Sequence[str] = SEARCH_SUBDIRS) -> Path:
    """Where a referenced image should be, following the Obsidian lookup order."""
    base_dir = Path(base_dir)
    for sub in extra_dirs:
        candidate = base_dir / sub / name if sub else base_dir / name
        if candidate.exists():
            return candidate
    return base_dir / name


def collect(
    note_path: Path,
    frames_dir: Path,
    out_dir: Path,
) -> Tuple[int, List[str]]:
    """Copy every referenced image into ``out_dir``.

    Returns ``(referenced, missing)``. A missing file is reported rather than
    skipped: an embed pointing at nothing is a broken note, not a detail.
    """
    note_path = Path(note_path)
    refs = referenced_images(note_path.read_text(encoding="utf-8"))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    missing: List[str] = []
    for name in refs:
        source = Path(frames_dir) / name
        if not source.exists():
            source = resolve(name, note_path.parent)
        if source.exists():
            shutil.copy2(source, out_dir / name)
        else:
            missing.append(name)
    return len(refs), missing


__all__ = [
    "EMBED",
    "IMG_EXT",
    "SEARCH_SUBDIRS",
    "collect",
    "referenced_images",
    "resolve",
]
