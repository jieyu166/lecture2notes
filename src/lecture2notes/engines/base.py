"""Engine interface, cue model and the SRT primitives every engine shares.

Ported from rad-workflow skills/whisper-srt-zh/scripts/transcribe.py
(timestamp formatting and the cue writing loop of ``run_faster_whisper``) and
skills/lecture-to-notes/scripts/build_lecture_viewer.py (``parse_srt``).

An engine is anything that turns audio into cues. Everything downstream of an
engine works on :class:`Cue` objects, so a new backend only has to produce those.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)

# A cue shorter than this after time calibration is widened instead of being
# emitted with a zero or negative duration, which some players drop silently.
MIN_CUE_DURATION = 0.3


@dataclass
class Cue:
    """One subtitle cue: a time range plus its text."""

    start: float
    end: float
    text: str

    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> Dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text}


@dataclass(frozen=True)
class EngineMeta:
    """What the CLI needs to know about an engine before running it.

    ``local`` drives the ``--allow-cloud`` gate: an engine that is not local
    sends audio off the machine, so it must be asked for explicitly.
    """

    name: str
    local: bool
    needs_gpu: bool
    has_timestamps: bool
    default_model: Optional[str] = None
    extra: str = ""

    # ``native_timestamps`` is the name used in the design document; keep both so
    # neither spelling breaks a caller.
    @property
    def native_timestamps(self) -> bool:
        return self.has_timestamps

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["native_timestamps"] = self.has_timestamps
        return data


@dataclass(frozen=True)
class DependencyStatus:
    """Whether an engine could run right now, and what is missing if not.

    ``--list-engines`` must answer this for four engines in well under a second,
    so a probe never imports a model runtime and never touches a weight file
    beyond asking the filesystem whether it is there.
    """

    name: str
    satisfied: bool
    detail: str = ""

    def mark(self) -> str:
        return "ready" if self.satisfied else "missing"

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "satisfied": self.satisfied, "detail": self.detail}


class Engine:
    """Base class for every transcription backend.

    Subclasses declare :attr:`meta` and implement :meth:`transcribe`. They must
    raise ``lecture2notes._deps.MissingDependency`` before opening any file when
    their runtime requirement is absent, so the CLI can exit 3 without writing.
    """

    meta: EngineMeta = EngineMeta(
        name="base", local=True, needs_gpu=False, has_timestamps=False
    )

    def __init__(self, **options: Any) -> None:
        self.options: Dict[str, Any] = dict(options)

    @property
    def name(self) -> str:
        return self.meta.name

    def check(self) -> None:
        """Raise MissingDependency if this engine cannot run on this machine."""
        raise NotImplementedError

    def probe(self) -> DependencyStatus:
        """Report dependency state without importing or loading anything heavy.

        The default runs :meth:`check` and catches the dependency error, which is
        correct but may import a runtime. An engine whose ``check`` is expensive
        overrides this with a ``find_spec``/``Path.exists`` version.
        """
        from lecture2notes import _deps

        try:
            self.check()
        except _deps.MissingDependency as exc:
            return DependencyStatus(self.meta.name, False, "%s (%s)" % (exc.name, exc.how))
        return DependencyStatus(self.meta.name, True, "")

    def transcribe(self, audio: Path, lang: str) -> List[Cue]:
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<Engine %s>" % self.meta.name


# -- SRT primitives --------------------------------------------------------


def format_timestamp(sec: float) -> str:
    """Seconds to ``HH:MM:SS,mmm``. Negative input clamps to zero."""
    if sec < 0:
        sec = 0.0
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def parse_timestamp(text: str) -> float:
    """``HH:MM:SS,mmm`` or ``HH:MM:SS.mmm`` or ``MM:SS`` to seconds."""
    parts = text.strip().replace(",", ".").split(":")
    if not 2 <= len(parts) <= 3:
        raise ValueError("unparseable timestamp: %s" % text)
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


def read_subtitle_text(path: Path) -> str:
    """Decode a subtitle file by BOM, never guessing UTF-16 from a decode error.

    A single bad byte in a UTF-8 transcript must not flip the whole file to
    UTF-16 and turn it into mojibake, so the fallback is lossy UTF-8.
    """
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig")
    return raw.decode("utf-8", "replace")


def parse_srt_text(text: str) -> List[Cue]:
    """Parse SRT or VTT cue text into :class:`Cue` objects.

    Index lines are recognised by "the next line is a timecode", because a bare
    number can also be part of the subtitle text.
    """
    lines = text.replace("\r", "").split("\n")
    cues: List[Cue] = []
    buckets: List[List[str]] = []
    for i, line in enumerate(lines):
        match = SRT_TIME.search(line)
        if match:
            groups = [int(x) for x in match.groups()]
            start = groups[0] * 3600 + groups[1] * 60 + groups[2] + groups[3] / 1000
            end = groups[4] * 3600 + groups[5] * 60 + groups[6] + groups[7] / 1000
            cues.append(Cue(start=start, end=end, text=""))
            buckets.append([])
            continue
        stripped = line.strip()
        if not cues or not stripped:
            continue
        if stripped.isdigit() and any(SRT_TIME.search(nxt) for nxt in lines[i + 1:i + 2]):
            continue  # index line
        buckets[-1].append(stripped)
    out: List[Cue] = []
    for cue, body in zip(cues, buckets):
        joined = " ".join(body).strip()
        if joined:
            out.append(Cue(start=round(cue.start, 3), end=round(cue.end, 3), text=joined))
    return out


def parse_srt(path: Path) -> List[Cue]:
    return parse_srt_text(read_subtitle_text(Path(path)))


def cues_to_srt(cues: Sequence[Cue]) -> str:
    """Serialise cues as SRT: numbered from 1, blank line between blocks."""
    blocks = []
    for number, cue in enumerate(cues, 1):
        blocks.append(
            "%d\n%s --> %s\n%s\n"
            % (number, format_timestamp(cue.start), format_timestamp(cue.end), cue.text)
        )
    return "\n".join(blocks)


def write_srt(path: Path, cues: Sequence[Cue]) -> Path:
    """Write SRT as UTF-8 without BOM and with LF line endings."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(cues_to_srt(cues), encoding="utf-8", newline="\n")
    return destination


__all__ = [
    "Cue",
    "DependencyStatus",
    "Engine",
    "EngineMeta",
    "MIN_CUE_DURATION",
    "SRT_TIME",
    "cues_to_srt",
    "format_timestamp",
    "parse_srt",
    "parse_srt_text",
    "parse_timestamp",
    "read_subtitle_text",
    "write_srt",
]
