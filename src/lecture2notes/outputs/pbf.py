"""PotPlayer chapter bookmarks (.pbf) from the canonical JSON.

Ported from rad-workflow skills/lecture-to-notes/scripts/json_to_pbf.py.

PotPlayer loads a ``.pbf`` sitting next to the video with the same base name, and
shows its entries as clickable chapters on the seek bar. So the output filename
is matched to the actual video file rather than to the JSON: a JSON named after
the lecture and a video named after the recording session would otherwise
produce a chapter file the player never opens.

Format is INI-like: ``index=<milliseconds>*<title>*``. An asterisk inside a title
would split the field, so it is replaced with its full-width form.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, List, Mapping, Optional, Tuple

VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts")
#: JSON files that are not lecture documents.
DERIVED_SUFFIXES = (".frames.json", ".frames_ocr.json", ".corrections.json")


def pbf_lines(data: Mapping[str, Any]) -> List[str]:
    """The chapter file's lines, one per segment. Pure."""
    segments = data.get("segments") or []
    lines = ["[Bookmark]"]
    for index, segment in enumerate(segments):
        milliseconds = int(round(float(segment["start_sec"]) * 1000))
        title = str(segment.get("title", "")).replace("*", "＊").replace("\n", " ").strip()
        lines.append("%d=%d*%s*" % (index, milliseconds, title))
    return lines


def pbf_text(data: Mapping[str, Any]) -> str:
    return "\n".join(pbf_lines(data)) + "\n"


def match_video_stem(json_path: Path) -> Tuple[str, str]:
    """Pick the output base name, preferring a real video file beside the JSON."""
    json_path = Path(json_path)
    json_stem = json_path.stem
    video_stems = [
        path.stem for path in json_path.parent.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXT
    ]
    if json_stem in video_stems:
        return json_stem, "video: exact name"
    prefixed = [
        stem for stem in video_stems
        if stem.startswith(json_stem) or json_stem.startswith(stem)
    ]
    if len(prefixed) == 1:
        return prefixed[0], "video: prefix match (%s)" % prefixed[0]
    if len(prefixed) > 1:
        return json_stem, "ambiguous: %d videos match, using the JSON name" % len(prefixed)
    return json_stem, "no matching video, using the JSON name"


def write_pbf(
    json_path: Path,
    data: Mapping[str, Any],
    out: Optional[Path] = None,
    bom: bool = False,
) -> Path:
    """Write the chapter file. ``bom`` exists for the players that need one."""
    json_path = Path(json_path)
    if out is None:
        stem, _note = match_video_stem(json_path)
        out = json_path.parent / (stem + ".pbf")
    Path(out).write_text(
        pbf_text(data), encoding="utf-8-sig" if bom else "utf-8", newline="\n"
    )
    return Path(out)


def is_lecture_json(path: Path) -> bool:
    name = Path(path).name
    return (
        Path(path).suffix.lower() == ".json"
        and not name.startswith("_")
        and not name.endswith(DERIVED_SUFFIXES)
    )


def iter_lecture_jsons(target: Path) -> Iterator[Path]:
    target = Path(target)
    if target.is_file():
        yield target
        return
    for path in sorted(target.glob("*.json")):
        if is_lecture_json(path):
            yield path


def chapter_count(data: Mapping[str, Any]) -> int:
    return len(data.get("segments") or [])


#: What ``outputs.toml`` calls the switch, and what a profile means by "off".
CONFIG_KEY = "pbf"
DEFAULT_ENABLED = False
#: Strings a TOML-ish or command-line value may use for the two states.
_TRUE_WORDS = ("true", "yes", "on", "1")
_FALSE_WORDS = ("false", "no", "off", "0", "")


def is_enabled(config: Optional[Mapping[str, Any]] = None) -> bool:
    """Is the chapter file switched on by the effective configuration?

    Pure, and the only place the answer is decided, so ``run`` cannot drift from
    what ``l2n profile show`` prints. The default is off: a ``.pbf`` beside a
    video changes what PotPlayer does with that video, and a pipeline that
    quietly starts rewriting a player's chapter list for everyone is a
    surprise, not a feature. The built-in generic profile therefore never sets
    it, and an absent key is a firm ``False`` rather than a maybe.

    Both an already-scoped mapping (``{"pbf": true}``, which is what a parsed
    ``outputs.toml`` section gives) and a whole config carrying an ``outputs``
    table are accepted, because the caller should not have to know which layer
    it is holding.
    """
    if not isinstance(config, Mapping):
        return DEFAULT_ENABLED
    scope: Mapping[str, Any] = config
    if CONFIG_KEY not in scope:
        outputs = config.get("outputs")
        if isinstance(outputs, Mapping):
            scope = outputs
    if CONFIG_KEY not in scope:
        return DEFAULT_ENABLED
    value = scope.get(CONFIG_KEY)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
    # Anything else is a malformed switch, and a malformed switch must not be
    # read as "on" -- that is the direction that writes files nobody asked for.
    return DEFAULT_ENABLED


__all__ = [
    "CONFIG_KEY",
    "DEFAULT_ENABLED",
    "DERIVED_SUFFIXES",
    "VIDEO_EXT",
    "chapter_count",
    "is_enabled",
    "is_lecture_json",
    "iter_lecture_jsons",
    "match_video_stem",
    "pbf_lines",
    "pbf_text",
    "write_pbf",
]
