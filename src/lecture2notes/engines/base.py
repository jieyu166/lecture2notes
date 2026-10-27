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

#: One cue timing line. The hour field is optional because WebVTT writes
#: ``01:02.500`` for a cue under an hour; groups are (h, m, s, frac) twice, with
#: a missing hour reported as None.
SRT_TIME = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})"
)

#: WebVTT inline markup: class, voice, styling and karaoke timestamp tags. Kept
#: to the tag names the format defines, because a bare ``<`` is ordinary text
#: in a medical transcript ("<5 mm") and must survive.
VTT_TAG = re.compile(
    r"</?(?:c|i|b|u|v|lang|ruby|rt)(?:[.\s][^<>]*)?>"
    r"|<(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3}>"
)
#: ASS override blocks some SRT exporters leave in, e.g. ``{\an8}``.
ASS_TAG = re.compile(r"\{\\[^{}]*\}")
#: The escapes WebVTT requires for its own markup characters.
VTT_ENTITIES = (
    ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " "),
    ("&lrm;", ""), ("&rlm;", ""), ("&amp;", "&"),
)
#: A WebVTT block that is not a cue and has no place in a transcript.
VTT_META_BLOCK = re.compile(r"^(?:NOTE|STYLE|REGION)(?:\s|$)")

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


def _clock_seconds(hours: Optional[str], minutes: str, seconds: str, frac: str) -> float:
    # "1" after the comma is a tenth of a second, not a millisecond.
    return (
        int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
        + int(frac.ljust(3, "0")[:3]) / 1000
    )


def clean_cue_text(text: str, vtt: bool = False) -> str:
    """Remove subtitle markup from one cue's text and collapse its whitespace."""
    text = ASS_TAG.sub("", VTT_TAG.sub("", text))
    if vtt:
        for entity, plain in VTT_ENTITIES:
            text = text.replace(entity, plain)
    return " ".join(text.split())


def parse_srt_text(text: str) -> List[Cue]:
    """Parse SRT or VTT cue text into :class:`Cue` objects.

    Accepts CRLF, a BOM that survived decoding, an unpadded or missing hour,
    WebVTT headers, cue identifiers, NOTE/STYLE/REGION blocks and inline tags.
    A line directly above a timing line is that cue's index or identifier,
    which is how a bare number inside the subtitle text is told apart from one.
    Every cue is kept, in order: merging repeats is :func:`dedupe_cues`' job,
    because the translation and calibration steps need the cue count intact.
    """
    text = text.lstrip("\ufeff")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    vtt = bool(lines) and lines[0].strip().startswith("WEBVTT")
    cues: List[Cue] = []
    buckets: List[List[str]] = []
    in_meta = False
    previous_blank = True
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            in_meta = False
            previous_blank = True
            continue
        was_blank, previous_blank = previous_blank, False
        if in_meta:
            continue
        match = SRT_TIME.search(line)
        if match:
            g = match.groups()
            cues.append(Cue(
                start=_clock_seconds(*g[0:4]), end=_clock_seconds(*g[4:8]), text=""
            ))
            buckets.append([])
            continue
        if was_blank and vtt and VTT_META_BLOCK.match(stripped):
            in_meta = True
            continue
        if any(SRT_TIME.search(nxt) for nxt in lines[i + 1:i + 2]):
            continue  # index or cue identifier
        if not cues:
            continue  # WEBVTT header and anything else before the first cue
        buckets[-1].append(stripped)
    out: List[Cue] = []
    for cue, body in zip(cues, buckets):
        joined = clean_cue_text(" ".join(body), vtt)
        if joined:
            out.append(Cue(start=round(cue.start, 3), end=round(cue.end, 3), text=joined))
    return out


def dedupe_cues(cues: Sequence[Cue]) -> List[Cue]:
    """Merge repeated and rolling captions into one cue per thing said.

    Two patterns, both common in exported and auto-generated subtitles:

    * the same text in consecutive cues (a caption re-emitted to extend it) --
      merged into one cue spanning both;
    * a rolling caption that repeats the end of the previous cue before adding
      to it ("a b" then "b c") -- the repeated part is dropped, so "c" is all
      that remains of the second cue. The overlap must fall on line breaks
      (joined as spaces) on both sides, so a cue that merely starts with the
      same word is left alone.
    """
    out: List[Cue] = []
    previous = ""
    for cue in cues:
        text = cue.text
        if out and text == previous:
            last = out[-1]
            out[-1] = Cue(start=last.start, end=max(last.end, cue.end), text=last.text)
            continue
        overlap = _rolling_overlap(previous, text)
        previous = text
        text = text[overlap:].strip()
        if text:
            out.append(Cue(start=cue.start, end=cue.end, text=text))
    return out


def _rolling_overlap(previous: str, text: str) -> int:
    """Length of the longest end of ``previous`` that ``text`` starts with."""
    for size in range(min(len(previous), len(text) - 1), 0, -1):
        if (
            text[size] == " "
            and previous.endswith(text[:size])
            and (size == len(previous) or previous[-size - 1] == " ")
        ):
            return size
    return 0


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
    "clean_cue_text",
    "cues_to_srt",
    "dedupe_cues",
    "format_timestamp",
    "parse_srt",
    "parse_srt_text",
    "parse_timestamp",
    "read_subtitle_text",
    "write_srt",
]
