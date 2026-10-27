"""``lines``: the transcript inside the canonical document (schema 2.1).

The viewer used to read the subtitle file beside the JSON at build time. A
document that carries its own transcript can be searched, quoted with
timecodes and shown bilingually by anything that reads the JSON alone, which
is what ``lines`` is for: one object per thing said, ``{t, end, text, zh?}``.

The lines are derived, never written by hand: ``l2n lines`` rebuilds them from
the subtitle with repeated and rolling captions merged (``dedupe_cues``), and
keeps a translation already attached to a line whose time and text did not
change. ``check json`` compares them with the subtitle again, so a transcript
corrected after the lines were built is reported instead of drifting silently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from lecture2notes.engines.base import Cue, dedupe_cue_groups, parse_srt_text, read_subtitle_text
from lecture2notes.schema.model import Finding

#: Line times are kept to the centisecond; finer than that is noise from the
#: subtitle's millisecond field and only makes the document harder to diff.
TIME_DIGITS = 2


def lines_from_cues(
    cues: Sequence[Cue], translations: Optional[Sequence[str]] = None
) -> List[Dict[str, Any]]:
    """One ``{t, end, text}`` per cue, after merging repeats.

    ``translations`` runs parallel to ``cues`` (one per input cue). A line made
    of several cues gets their distinct translations, in order; a rolling
    caption keeps the translation of its whole cue, because the translator saw
    the whole cue and not the remainder left after the overlap was dropped.
    """
    out: List[Dict[str, Any]] = []
    for cue, members in dedupe_cue_groups(cues):
        line: Dict[str, Any] = {
            "t": round(cue.start, TIME_DIGITS), "end": round(cue.end, TIME_DIGITS), "text": cue.text,
        }
        if translations is not None:
            parts: List[str] = []
            for position in members:
                zh = translations[position].strip() if position < len(translations) else ""
                if zh and zh not in parts:
                    parts.append(zh)
            if parts:
                line["zh"] = " ".join(parts)
        out.append(line)
    return out


def _key(line: Mapping[str, Any]) -> Tuple[float, str]:
    return (round(float(line.get("t") or 0), TIME_DIGITS), str(line.get("text") or ""))


def keep_translations(
    fresh: Sequence[Mapping[str, Any]], previous: Any
) -> List[Dict[str, Any]]:
    """``fresh`` with each ``zh`` carried over from an unchanged previous line.

    A line whose text or start changed loses its translation on purpose: the
    old one translates words that are no longer there.
    """
    known: Dict[Tuple[float, str], str] = {}
    if isinstance(previous, list):
        for line in previous:
            if isinstance(line, Mapping) and isinstance(line.get("zh"), str):
                known[_key(line)] = line["zh"]
    out: List[Dict[str, Any]] = []
    for line in fresh:
        entry = dict(line)
        zh = known.get(_key(entry))
        if zh is not None:
            entry["zh"] = zh
        out.append(entry)
    return out


def subtitle_for(json_path: Path, data: Mapping[str, Any]) -> Optional[Path]:
    """The subtitle a document's lines come from: ``source.subtitle.path``, else ``<stem>.srt``."""
    json_path = Path(json_path)
    source = data.get("source")
    subtitle = source.get("subtitle") if isinstance(source, Mapping) else None
    name = subtitle.get("path") if isinstance(subtitle, Mapping) else None
    candidates = []
    if isinstance(name, str) and name:
        candidates.append(json_path.parent / name)
    candidates += [json_path.with_suffix(".srt"), json_path.with_suffix(".vtt")]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def read_lines(subtitle: Path) -> List[Dict[str, Any]]:
    return lines_from_cues(parse_srt_text(read_subtitle_text(Path(subtitle))))


def stale_findings(json_path: Path, data: Mapping[str, Any]) -> List[Finding]:
    """A warning when ``lines`` no longer matches the subtitle beside the document."""
    lines = data.get("lines")
    if not isinstance(lines, list) or not lines:
        return []
    subtitle = subtitle_for(json_path, data)
    if subtitle is None:
        return []
    try:
        fresh = read_lines(subtitle)
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    current = [_key(line) for line in lines if isinstance(line, Mapping)]
    if current == [_key(line) for line in fresh]:
        return []
    return [Finding(
        "warn", "lines_stale",
        "lines (%d) differ from %s (%d after merging repeats); rebuild with: "
        "l2n lines %s --force" % (len(current), subtitle.name, len(fresh), Path(json_path).name),
        location="lines",
    )]


__all__ = [
    "TIME_DIGITS",
    "keep_translations",
    "lines_from_cues",
    "read_lines",
    "stale_findings",
    "subtitle_for",
]
