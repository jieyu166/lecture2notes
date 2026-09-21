"""Condense a subtitle track into one line per minute, for reading and segmenting.

Consolidated from a transcript-condensing procedure script written for this
project. The original hard-coded three filenames and a network share; here the
paths are arguments and the bucket size is a parameter.

Why condense at all: deciding where a lecture's segments begin means reading the
whole transcript, and a three-hour SRT is tens of thousands of cue blocks of
which ninety percent is timecode. One line per minute keeps the position
information that matters for segmenting and drops everything else, which is what
makes the transcript readable in one pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Mapping, Sequence

from lecture2notes.engines.base import parse_srt_text, read_subtitle_text

DEFAULT_BUCKET_SEC = 60


def bucket_cues(text: str, bucket_sec: int = DEFAULT_BUCKET_SEC) -> Dict[int, List[str]]:
    """Group cue texts by ``floor(start / bucket_sec)``."""
    if bucket_sec <= 0:
        raise ValueError("bucket_sec must be positive")
    buckets: Dict[int, List[str]] = {}
    for cue in parse_srt_text(text):
        key = int(cue.start) // bucket_sec
        buckets.setdefault(key, []).append(cue.text.strip())
    return buckets


def format_label(bucket: int, bucket_sec: int = DEFAULT_BUCKET_SEC) -> str:
    """``[  0:00]`` style label for the start of a bucket."""
    start = bucket * bucket_sec
    return "[%3d:%02d]" % (start // 3600, (start % 3600) // 60)


def condense_text(text: str, bucket_sec: int = DEFAULT_BUCKET_SEC) -> str:
    """One line per bucket: a timestamp label then that bucket's text joined."""
    buckets = bucket_cues(text, bucket_sec)
    lines = [
        "%s %s" % (format_label(key, bucket_sec), " ".join(buckets[key]).strip())
        for key in sorted(buckets)
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def condense_file(
    source: Path,
    destination: Path,
    bucket_sec: int = DEFAULT_BUCKET_SEC,
) -> Path:
    """Condense a subtitle file to a plain text file, UTF-8 without BOM."""
    text = read_subtitle_text(Path(source))
    out = Path(destination)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(condense_text(text, bucket_sec), encoding="utf-8", newline="\n")
    return out


def stats(text: str, bucket_sec: int = DEFAULT_BUCKET_SEC) -> Dict[str, int]:
    buckets = bucket_cues(text, bucket_sec)
    return {
        "buckets": len(buckets),
        "chars": sum(len(item) for values in buckets.values() for item in values),
    }


__all__ = [
    "DEFAULT_BUCKET_SEC",
    "read_subtitle_text",
    "bucket_cues",
    "condense_file",
    "condense_text",
    "format_label",
    "stats",
]
