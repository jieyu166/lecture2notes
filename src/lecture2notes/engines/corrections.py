"""Deterministic ASR correction tables, with a raw backup and an audit sidecar.

Ported from rad-workflow skills/whisper-srt-zh/scripts/correct_srt.py.

Automatic find-and-replace is a risky operation: the "correct" word is often
simply absent from the context the table was written for, and one bad
replacement destroys the original evidence beyond recovery. So every write
leaves two artefacts behind: ``<name>.raw.<ext>`` says what the text used to
look like, and ``<name>.corrections.json`` says which rule changed it and how
many times.

Longer keys are applied first, otherwise a short key eats part of a longer one
before the longer rule ever gets a chance to match.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from lecture2notes import _out
from lecture2notes.schema.io import write_json_atomic

#: Table sections that are applied automatically. A ``context_sensitive``
#: section is deliberately never applied: those need a human or an LLM to judge.
AUTO_SECTIONS = ("deterministic", "radiology")

#: OpenCC configuration for Simplified to Traditional with Taiwanese word choice.
S2T_CONFIG = "s2twp"

Pair = Tuple[str, str, str]  # (heard, correct, source)


def load_table(data: Mapping[str, Any], sections: Sequence[str] = AUTO_SECTIONS) -> List[Pair]:
    """Flatten a corrections document into ``(heard, correct, source)``, longest first.

    A later section overrides an earlier one for the same key, which keeps a
    profile overlay able to win over the built-in table.
    """
    pairs: Dict[str, Tuple[str, str]] = {}
    for source in sections:
        section = data.get(source) or {}
        if not isinstance(section, Mapping):
            continue
        for heard, correct in section.items():
            pairs[str(heard)] = (str(correct), source)
    return sorted(
        ((heard, correct, source) for heard, (correct, source) in pairs.items()),
        key=lambda item: len(item[0]),
        reverse=True,
    )


def load_table_file(path: Path, sections: Sequence[str] = AUTO_SECTIONS) -> List[Pair]:
    return load_table(json.loads(Path(path).read_text(encoding="utf-8")), sections)


def apply_corrections(text: str, pairs: Iterable[Pair]) -> Tuple[str, List[Dict[str, Any]]]:
    """Apply the table and report every rule that fired.

    Returns the corrected text and one record per rule: ``heard``, ``correct``,
    ``count`` and ``source``. The record shape is the sidecar's shape.
    """
    hits: List[Dict[str, Any]] = []
    for heard, correct, source in pairs:
        if heard and heard in text:
            count = text.count(heard)
            text = text.replace(heard, correct)
            hits.append({"heard": heard, "correct": correct, "count": count, "source": source})
    return text, hits


def convert_simplified(text: str) -> Tuple[str, bool]:
    """Convert Simplified to Traditional if OpenCC is installed; never fail hard.

    Missing OpenCC is a degradation the user can see, not a reason to refuse to
    write subtitles, so this returns ``(text, False)`` instead of raising.
    """
    try:
        from opencc import OpenCC  # type: ignore
    except ImportError:
        return text, False
    return OpenCC(S2T_CONFIG).convert(text), True


def backup_raw(path: Path) -> Optional[Path]:
    """Copy ``x.srt`` to ``x.raw.srt`` once; never overwrite an existing backup."""
    source = Path(path)
    backup = source.with_suffix(".raw%s" % source.suffix)
    if backup.exists():
        return backup
    shutil.copy2(source, backup)
    return backup


def build_sidecar(
    input_name: str,
    output_name: str,
    backup_name: Optional[str],
    hits: Sequence[Mapping[str, Any]],
    s2t_applied: bool,
    table_path: Optional[str] = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the audit record. Kept pure so its shape can be asserted directly."""
    return {
        "input": input_name,
        "output": output_name,
        "backup": backup_name,
        "generated_at": generated_at
        or datetime.now().astimezone().isoformat(timespec="seconds"),
        "corrections_table": table_path,
        "s2t": {"applied": bool(s2t_applied), "converter": S2T_CONFIG if s2t_applied else None},
        "total_kinds": len(hits),
        "total_hits": sum(int(hit.get("count", 0)) for hit in hits),
        # The key matches the document's ``corrections[]`` so a later stage can
        # copy the list across without renaming anything.
        "corrections": [dict(hit) for hit in hits],
    }


def write_sidecar(target: Path, payload: Mapping[str, Any]) -> Path:
    """Write ``<stem>.corrections.json`` as UTF-8 without BOM, LF only."""
    path = Path(target).with_suffix(".corrections.json")
    return write_json_atomic(path, payload)


def correct_file(
    path: Path,
    pairs: Sequence[Pair],
    out: Optional[Path] = None,
    s2t: bool = True,
    backup: bool = True,
    sidecar: bool = True,
    table_path: Optional[str] = None,
    always: bool = False,
) -> Dict[str, Any]:
    """Correct a subtitle or transcript file in place, preserving its structure.

    Only the text is touched: cue numbers, timecodes and blank lines survive
    because the replacement is a plain string substitution over the whole file.

    ``always`` writes the backup and the sidecar even when no rule fired, which
    is what the transcribe stage wants: "a table was applied and changed
    nothing" and "no table was ever applied" must not look the same on disk.
    """
    from lecture2notes.engines.base import read_subtitle_text

    source = Path(path)
    text = read_subtitle_text(source).replace("\r", "")
    original = text
    text, s2t_applied = convert_simplified(text) if s2t else (text, False)
    fixed, hits = apply_corrections(text, pairs)

    result: Dict[str, Any] = {
        "changed": fixed != original,
        "hits": hits,
        "s2t": s2t_applied,
        "output": None,
        "backup": None,
        "sidecar": None,
    }
    if not result["changed"] and not always:
        _out.say("skip", "no change: %s" % source.name)
        return result

    destination = Path(out) if out else source
    backup_path = backup_raw(source) if (backup and destination == source) else None
    destination.write_text(fixed, encoding="utf-8", newline="\n")
    result["output"] = str(destination)
    result["backup"] = str(backup_path) if backup_path else None

    if sidecar:
        payload = build_sidecar(
            source.name,
            destination.name,
            backup_path.name if backup_path else None,
            hits,
            s2t_applied,
            table_path,
        )
        result["sidecar"] = str(write_sidecar(destination, payload))
    _out.ok("%d rules applied, %d replacements" % (len(hits), sum(h["count"] for h in hits)))
    return result


__all__ = [
    "AUTO_SECTIONS",
    "S2T_CONFIG",
    "apply_corrections",
    "backup_raw",
    "build_sidecar",
    "convert_simplified",
    "correct_file",
    "load_table",
    "load_table_file",
    "write_sidecar",
]
