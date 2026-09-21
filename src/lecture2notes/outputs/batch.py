"""Discover the lectures in a course folder and report what each one still needs.

Ported from rad-workflow skills/lecture-to-notes/scripts/batch_course.py
(``load`` and ``lectures``).

The original also drove the pipeline by shelling out to the other scripts in
order. That job belongs to ``l2n run``, so only the discovery half is ported:
given a folder, work out which lectures are there, which media each one has, and
which stages have already produced output.

That last part is what makes a batch resumable. A run that stops halfway must be
restartable without redoing the hours of work it already finished, so "has
frames" and "has OCR" are read off the documents rather than assumed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi")
DERIVED_SUFFIXES = (".frames.json", ".frames_ocr.json", ".corrections.json")


@dataclass
class Lecture:
    """One lecture's inputs and the stages it has already completed."""

    json_path: Path
    video: Optional[Path]
    srt: Optional[Path]
    segments: int
    has_frames: bool
    has_ocr: bool
    has_viewer: bool

    @property
    def stem(self) -> str:
        return self.json_path.stem

    def pending(self) -> List[str]:
        """Stages that still need to run, in pipeline order."""
        todo: List[str] = []
        if not self.has_frames:
            todo.append("frames")
        if not self.has_ocr:
            todo.append("ocr")
        if not self.has_viewer:
            todo.append("viewer")
        return todo

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stem": self.stem,
            "json": str(self.json_path),
            "video": str(self.video) if self.video else None,
            "srt": str(self.srt) if self.srt else None,
            "segments": self.segments,
            "has_frames": self.has_frames,
            "has_ocr": self.has_ocr,
            "has_viewer": self.has_viewer,
            "pending": self.pending(),
        }


def is_lecture_json(path: Path) -> bool:
    name = Path(path).name
    return not name.startswith("_") and not name.endswith(DERIVED_SUFFIXES)


def find_video(folder: Path, stem: str) -> Optional[Path]:
    return next(
        (path for path in sorted(Path(folder).iterdir())
         if path.suffix.lower() in VIDEO_EXT and path.stem.startswith(stem)),
        None,
    )


def find_subtitle(folder: Path, stem: str) -> Optional[Path]:
    """The corrected subtitle, never the ``.raw.srt`` the correction step kept."""
    return next(
        (path for path in sorted(Path(folder).glob("%s*.srt" % stem))
         if ".raw." not in path.name),
        None,
    )


def inspect_document(data: Mapping[str, Any]) -> Dict[str, Any]:
    """What a document says about its own progress. Pure."""
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        return {"segments": 0, "has_frames": False, "has_ocr": False}
    return {
        "segments": len(segments),
        "has_frames": any(
            s.get("frames") or s.get("frame") for s in segments if isinstance(s, Mapping)
        ),
        "has_ocr": any(
            s.get("frame_ocr") for s in segments if isinstance(s, Mapping)
        ),
    }


def discover(folder: Path, only: Optional[str] = None) -> List[Lecture]:
    """Every lecture in the folder, optionally filtered by a filename substring."""
    folder = Path(folder)
    found: List[Lecture] = []
    for json_path in sorted(folder.glob("*.json")):
        if not is_lecture_json(json_path):
            continue
        if only and only not in json_path.name:
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            continue
        state = inspect_document(data)
        if not state["segments"]:
            continue
        found.append(Lecture(
            json_path=json_path,
            video=find_video(folder, json_path.stem),
            srt=find_subtitle(folder, json_path.stem),
            segments=state["segments"],
            has_frames=state["has_frames"],
            has_ocr=state["has_ocr"],
            has_viewer=(folder / ("%s.viewer.html" % json_path.stem)).exists(),
        ))
    return found


__all__ = [
    "DERIVED_SUFFIXES",
    "Lecture",
    "VIDEO_EXT",
    "discover",
    "find_subtitle",
    "find_video",
    "inspect_document",
    "is_lecture_json",
]
