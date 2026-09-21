"""Measure and correct the time offset of an official subtitle track.

Consolidated from two procedure scripts written for this project: the offset
measurement pass and the linear correction pass.

Why this exists: an official subtitle file is far more accurate on terminology
than any ASR run, so re-transcribing a three-hour recording to fix its timing
would trade good text for bad. Instead a few short ASR probes are used purely as
a clock, and only the timecodes are rewritten.

Why each measurement is built the way it is:

* Comparing cue start times alone carries the whole cue granularity as noise.
* Assuming an even speaking rate inside a probe window is worse still.

So both sides use their own real timecodes: every probe cue is located in the
official text by longest common substring, the hit is mapped back to the
official cue that contains it, and the position inside that cue is interpolated
by character proportion. One 90-second probe therefore yields ten to twenty
independent measurements, and the median of those is the probe's offset.

Across probes the offsets are fitted with least squares as ``offset(t) = a + b*t``,
because a long recording drifts: a single constant shift is right at one end of
the file and visibly wrong at the other.

Every function here is pure. The ASR probe itself is injected as an Engine, so
tests can calibrate against a fake engine with no model, GPU or network.
"""

from __future__ import annotations

import difflib
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from lecture2notes.engines.base import MIN_CUE_DURATION, Cue

#: Punctuation and whitespace are dropped before matching: the official track and
#: an ASR run never agree on them, and they would dominate a substring match.
PUNCTUATION = re.compile(r"[，。、,.\s！？!?：:；;（）()「」]")

#: A probe cue shorter than this carries too little signal to locate.
MIN_PROBE_CHARS = 10
#: A match must cover at least this fraction of the probe cue.
MIN_MATCH_RATIO = 0.5
#: ...and at least this many characters, whichever is larger.
MIN_MATCH_CHARS = 8
#: Fewer reliable points than this and the probe is discarded.
MIN_POINTS_PER_PROBE = 3
#: Spread across probes at or above this many seconds means real drift.
DRIFT_THRESHOLD = 1.5


@dataclass(frozen=True)
class ProbeResult:
    """One probe window's measurement."""

    at_sec: float
    n_points: int
    median_offset: Optional[float]
    min_offset: Optional[float]
    max_offset: Optional[float]

    @property
    def reliable(self) -> bool:
        return self.n_points >= MIN_POINTS_PER_PROBE and self.median_offset is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "at_sec": self.at_sec,
            "n_points": self.n_points,
            "median_offset": None if self.median_offset is None else round(self.median_offset, 3),
            "min": None if self.min_offset is None else round(self.min_offset, 3),
            "max": None if self.max_offset is None else round(self.max_offset, 3),
        }


def normalize(text: str) -> str:
    """Strip punctuation and whitespace so two transcriptions can be compared."""
    return PUNCTUATION.sub("", text)


def flatten_cues(cues: Sequence[Cue]) -> Tuple[str, List[Tuple[int, int]]]:
    """Concatenate cue bodies and record where each cue starts in that string.

    Returns ``(joined, [(char_offset, cue_index), ...])``.
    """
    joined_parts: List[str] = []
    starts: List[Tuple[int, int]] = []
    position = 0
    for index, cue in enumerate(cues):
        body = normalize(cue.text)
        starts.append((position, index))
        joined_parts.append(body)
        position += len(body)
    return "".join(joined_parts), starts


def _owning_cue(starts: Sequence[Tuple[int, int]], char_index: int) -> Optional[Tuple[int, int]]:
    """Which cue does this character position belong to, and where does it start?"""
    found: Optional[Tuple[int, int]] = None
    for start, index in starts:
        if start <= char_index:
            found = (start, index)
        else:
            break
    return found


def measure_offsets(
    probe_cues: Sequence[Cue],
    official_cues: Sequence[Cue],
    base_sec: float = 0.0,
    min_probe_chars: int = MIN_PROBE_CHARS,
    min_match_ratio: float = MIN_MATCH_RATIO,
    min_match_chars: int = MIN_MATCH_CHARS,
) -> List[float]:
    """Return one offset measurement per probe cue that could be located.

    A positive offset means the official track is ahead of reality, so the
    correction subtracts it.
    """
    joined, starts = flatten_cues(official_cues)
    if not joined:
        return []
    offsets: List[float] = []
    for cue in probe_cues:
        body = normalize(cue.text)
        if len(body) < min_probe_chars:
            continue
        matcher = difflib.SequenceMatcher(None, joined, body)
        match = matcher.find_longest_match(0, len(joined), 0, len(body))
        if match.size < max(min_match_chars, len(body) * min_match_ratio):
            continue
        owner = _owning_cue(starts, match.a)
        if owner is None:
            continue
        cue_char_start, cue_index = owner
        official = official_cues[cue_index]
        official_body = normalize(official.text)
        fraction = (match.a - cue_char_start) / max(len(official_body), 1)
        official_sec = official.start + (official.end - official.start) * fraction
        actual_sec = base_sec + cue.start + (cue.end - cue.start) * (
            match.b / max(len(body), 1)
        )
        offsets.append(official_sec - actual_sec)
    return offsets


def summarize_probe(at_sec: float, offsets: Sequence[float]) -> ProbeResult:
    """Collapse one probe's measurements into a median plus its spread."""
    if not offsets:
        return ProbeResult(float(at_sec), 0, None, None, None)
    return ProbeResult(
        float(at_sec),
        len(offsets),
        statistics.median(offsets),
        min(offsets),
        max(offsets),
    )


def fit_linear(points: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    """Least-squares fit of ``offset(t) = a + b*t``.

    With a single point, or with all points at the same time, the slope is zero
    and the fit degenerates to a constant shift, which is the right answer there.
    """
    if not points:
        raise ValueError("cannot fit an empty point set")
    n = len(points)
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    denominator = n * sxx - sx * sx
    b = (n * sxy - sx * sy) / denominator if denominator else 0.0
    a = (sy - b * sx) / n
    return a, b


def offset_at(a: float, b: float, t: float) -> float:
    return a + b * t


def drift_span(medians: Sequence[float]) -> float:
    """How far apart the probe medians are; the drift signal."""
    if not medians:
        return 0.0
    return max(medians) - min(medians)


def has_drift(medians: Sequence[float], threshold: float = DRIFT_THRESHOLD) -> bool:
    return drift_span(medians) >= threshold


def apply_offset(
    cues: Sequence[Cue],
    a: float,
    b: float,
    min_duration: float = MIN_CUE_DURATION,
) -> List[Cue]:
    """Shift every timecode by ``offset(t)`` and leave the text untouched.

    Each endpoint is corrected with the offset at its own time, so a drifting
    file is stretched rather than slid. A cue that would come out zero-length or
    inverted is widened to ``min_duration`` instead of being emitted broken.
    """
    corrected: List[Cue] = []
    for cue in cues:
        start = max(cue.start - offset_at(a, b, cue.start), 0.0)
        end = max(cue.end - offset_at(a, b, cue.end), 0.0)
        if end - start < min_duration:
            end = start + min_duration
        corrected.append(Cue(start=start, end=end, text=cue.text))
    return corrected


def build_report(
    probes: Sequence[ProbeResult],
    a: float,
    b: float,
    threshold: float = DRIFT_THRESHOLD,
) -> Dict[str, Any]:
    """The ``.offset.json`` payload."""
    medians = [p.median_offset for p in probes if p.median_offset is not None]
    return {
        "probes": [probe.to_dict() for probe in probes],
        "fit": {"a": round(a, 6), "b": round(b, 9)},
        "drift": has_drift(medians, threshold),
        "span": round(drift_span(medians), 3),
    }


def probe_positions(duration_sec: float, count: int = 3, margin_sec: float = 300.0) -> List[float]:
    """Evenly spaced probe start times, kept clear of the opening and the tail."""
    if count < 1:
        raise ValueError("count must be at least 1")
    usable = max(duration_sec - margin_sec, 0.0)
    if count == 1 or usable <= margin_sec:
        return [min(margin_sec, max(duration_sec - 1.0, 0.0))]
    step = (usable - margin_sec) / (count - 1)
    return [round(margin_sec + step * index, 3) for index in range(count)]


def calibrate(
    official_cues: Sequence[Cue],
    probe_windows: Sequence[Tuple[float, Sequence[Cue]]],
    min_duration: float = MIN_CUE_DURATION,
    threshold: float = DRIFT_THRESHOLD,
) -> Tuple[List[Cue], Dict[str, Any]]:
    """Full calibration from already-transcribed probe windows.

    ``probe_windows`` is ``[(window_start_sec, cues_relative_to_that_start)]``,
    which is what a caller gets after handing each window to an Engine.
    Raises ValueError when fewer than three probes are reliable, because a fit
    over two points cannot tell drift from noise.
    """
    probes = [
        summarize_probe(at_sec, measure_offsets(cues, official_cues, base_sec=at_sec))
        for at_sec, cues in probe_windows
    ]
    usable = [probe for probe in probes if probe.reliable]
    if len(usable) < MIN_POINTS_PER_PROBE:
        raise ValueError(
            "only %d reliable probes (need %d); not writing a calibrated file"
            % (len(usable), MIN_POINTS_PER_PROBE)
        )
    a, b = fit_linear([(probe.at_sec, float(probe.median_offset)) for probe in usable])
    return apply_offset(official_cues, a, b, min_duration), build_report(probes, a, b, threshold)


def transcribe_probes(
    engine: Any,
    audio_windows: Sequence[Tuple[float, Path]],
    lang: str,
) -> List[Tuple[float, List[Cue]]]:
    """Run an Engine over pre-cut probe windows.

    Kept separate from :func:`calibrate` so the measurement logic stays testable
    with no engine at all.
    """
    return [(at_sec, engine.transcribe(path, lang)) for at_sec, path in audio_windows]


__all__ = [
    "DRIFT_THRESHOLD",
    "MIN_MATCH_CHARS",
    "MIN_MATCH_RATIO",
    "MIN_POINTS_PER_PROBE",
    "MIN_PROBE_CHARS",
    "ProbeResult",
    "apply_offset",
    "build_report",
    "calibrate",
    "drift_span",
    "fit_linear",
    "flatten_cues",
    "has_drift",
    "measure_offsets",
    "normalize",
    "offset_at",
    "probe_positions",
    "summarize_probe",
    "transcribe_probes",
]
