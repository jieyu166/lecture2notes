"""Course hub: lecture cards plus a cross-lecture search index.

Ported from rad-workflow skills/lecture-to-notes/scripts/build_course_hub.py.
Inspired by drpwchen/lecture-to-notes scripts/export_web.py @79053a3

A course is a folder of viewers, and without a hub the only way to find anything
is by filename. The hub does the two things a single viewer cannot: show the
lectures as cards, and search across all of them, with every hit deep-linking to
``<viewer>?t=<seconds>`` so the answer opens at the moment it was said.

The index covers slide OCR as well as titles and bullets, because a term that
was only ever on a slide is exactly the term nobody can find otherwise.

The HTML template itself is not ported: the hub page is rebuilt against schema
v2 later, and the data functions below are what it consumes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import unquote

from lecture2notes.frames.manifest import frame_seconds

#: Overrides file name, sitting in the course folder.
TITLES_FILE = "_titles.json"
DERIVED_SUFFIXES = (".frames.json", ".frames_ocr.json", ".corrections.json")
#: Where an unnumbered lecture sorts: after every numbered one.
UNNUMBERED_SORT_KEY = "zz"

#: ``<course>-<no>-<speaker with a title>-<topic>``
NAMED_PATTERN = re.compile(
    r"^(\d+)-(\d+)-([^-]+?醫師|[^-]+?主任|[^-]+?部長|[^-]+?教授)-(.+)$"
)
NUMBERED_PATTERN = re.compile(r"^\d+-(\d+)-(.+)$")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def parse_name(stem: str) -> Dict[str, str]:
    """Pull number, speaker and topic out of a filename.

    When the pattern does not match, the whole stem becomes the topic. Guessing
    harder to make the layout tidy would put wrong names on cards.
    """
    match = NAMED_PATTERN.match(stem)
    if match:
        return {"no": match.group(2), "speaker": match.group(3), "topic": match.group(4)}
    match = NUMBERED_PATTERN.match(stem)
    if match:
        return {"no": match.group(1), "speaker": "", "topic": match.group(2)}
    return {"no": "", "speaker": "", "topic": stem}


def load_titles(folder: Path) -> Dict[str, Dict[str, str]]:
    """Read the optional ``_titles.json`` override map.

    Shape: ``{"<stem>": {"no": "01", "speaker": "...", "topic": "..."}}``.
    A missing or unparseable file means no overrides, not a failure: the hub must
    still build from filenames alone.
    """
    path = Path(folder) / TITLES_FILE
    if not path.exists():
        return {}
    try:
        data = load_json(path)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def apply_titles(stem: str, titles: Mapping[str, Any]) -> Dict[str, str]:
    """Derive a card's fields from the filename, then let the override win.

    Only the keys present in the override are replaced, so a file can fix just
    the topic and keep the derived number.
    """
    card = parse_name(stem)
    override = titles.get(stem) or {}
    if isinstance(override, Mapping):
        for key in ("no", "speaker", "topic"):
            if key in override:
                card[key] = str(override[key])
    return card


def card_sort_key(card: Mapping[str, Any]) -> tuple:
    return (card.get("no") or UNNUMBERED_SORT_KEY, card.get("stem") or "")


def is_lecture_json(path: Path) -> bool:
    name = Path(path).name
    return not name.startswith("_") and not name.endswith(DERIVED_SUFFIXES)


def build_card(json_path: Path, data: Mapping[str, Any], titles: Mapping[str, Any]) -> Dict[str, Any]:
    """One lecture's card data."""
    json_path = Path(json_path)
    segments = data.get("segments") or []
    card = apply_titles(json_path.stem, titles)
    frames = [frame for segment in segments for frame in (segment.get("frames") or [])]
    card.update({
        "stem": json_path.stem,
        "viewer": "%s.viewer.html" % json_path.stem,
        "segments": segments,
        "frames": frames,
        "thumb": frames[0] if frames else None,
        "minutes": round(float(segments[-1].get("end_sec") or 0) / 60) if segments else 0,
        "summary": (data.get("overall_summary_zh") or "")[:150],
        "takeaways": data.get("takeaways_zh") or [],
    })
    return card


def collect(folder: Path, require_viewer: bool = True) -> List[Dict[str, Any]]:
    """Every lecture in the folder that has a viewer, as sorted cards."""
    folder = Path(folder)
    titles = load_titles(folder)
    cards: List[Dict[str, Any]] = []
    for json_path in sorted(folder.glob("*.json")):
        if not is_lecture_json(json_path):
            continue
        if require_viewer and not (folder / ("%s.viewer.html" % json_path.stem)).exists():
            continue
        try:
            data = load_json(json_path)
        except json.JSONDecodeError:
            continue
        if not (data.get("segments") or []):
            continue
        cards.append(build_card(json_path, data, titles))
    return sorted(cards, key=card_sort_key)


def build_index(cards: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The cross-lecture search index.

    Each row says which viewer to open, at which second, from which layer, and
    with what text.
    """
    index: List[Dict[str, Any]] = []
    for card in cards:
        for takeaway in card.get("takeaways") or []:
            index.append({
                "v": card["viewer"], "no": card.get("no", ""), "t": 0,
                "k": "takeaway", "x": str(takeaway),
            })
        for segment in card.get("segments") or []:
            second = int(float(segment.get("start_sec") or 0))
            index.append({
                "v": card["viewer"], "no": card.get("no", ""), "t": second,
                "k": "segment", "x": str(segment.get("title") or ""),
            })
            for bullet in segment.get("bullets_zh") or []:
                text = bullet["text"] if isinstance(bullet, Mapping) else str(bullet)
                index.append({
                    "v": card["viewer"], "no": card.get("no", ""), "t": second,
                    "k": "bullet", "x": text,
                })
            for entry in segment.get("frame_ocr") or []:
                text = (entry.get("text") or "").strip()
                if not text:
                    continue
                index.append({
                    "v": card["viewer"], "no": card.get("no", ""),
                    "t": frame_seconds(entry.get("frame", ""), second),
                    "k": "slide", "x": text,
                })
    return index


def missing_links(folder: Path, hrefs: Sequence[str]) -> List[str]:
    """Which relative links in the hub point at a file that is not there.

    Links are percent-decoded first, because the href is encoded and the file on
    disk is not.
    """
    folder = Path(folder)
    missing: List[str] = []
    for href in hrefs:
        if "://" in href or href.startswith("#"):
            continue
        target = folder / unquote(href.split("#")[0].split("?")[0])
        if not target.exists():
            missing.append(href)
    return missing


__all__ = [
    "DERIVED_SUFFIXES",
    "TITLES_FILE",
    "UNNUMBERED_SORT_KEY",
    "apply_titles",
    "build_card",
    "build_index",
    "card_sort_key",
    "collect",
    "is_lecture_json",
    "load_json",
    "load_titles",
    "missing_links",
    "parse_name",
]
