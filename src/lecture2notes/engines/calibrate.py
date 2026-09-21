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
import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from lecture2notes import _out
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



# -- probe placement -------------------------------------------------------

#: Each probe transcribes this many seconds of audio. Long enough to yield ten
#: to twenty independent measurements, short enough that three of them cost
#: minutes rather than an hour.
PROBE_WINDOW_SEC = 90.0
#: The opening of a recording is introductions and room noise, and the official
#: track is often padded there, so the first probe starts well past it.
FIRST_PROBE_SEC = 300.0
#: The last probe sits here rather than at the very end, where a file often has
#: credits, a fade-out or no speech at all.
LAST_PROBE_FRACTION = 0.92
#: Default number of probes. Three is the minimum a drift fit can use.
DEFAULT_PROBE_COUNT = 3


class CalibrationError(Exception):
    """Calibration cannot produce a trustworthy result; nothing was written."""


def default_probe_positions(
    duration_sec: float,
    count: int = DEFAULT_PROBE_COUNT,
    window_sec: float = PROBE_WINDOW_SEC,
) -> List[float]:
    """Where the probes go: 300 s, the midpoint and 92 percent of the duration.

    That exact layout is the documented default for three probes. Any other
    count is spread evenly between the first and last of those anchors, so
    ``--probes 5`` still brackets the same span and still measures drift across
    the whole recording rather than across its middle.
    """
    if count < 1:
        raise CalibrationError("need at least one probe")
    if duration_sec <= 0:
        raise CalibrationError("cannot place probes in a recording of unknown length")
    last = duration_sec * LAST_PROBE_FRACTION
    if count == DEFAULT_PROBE_COUNT:
        raw = [FIRST_PROBE_SEC, duration_sec / 2.0, last]
    elif count == 1:
        raw = [duration_sec / 2.0]
    else:
        step = (last - FIRST_PROBE_SEC) / (count - 1)
        raw = [FIRST_PROBE_SEC + step * index for index in range(count)]
    return clamp_positions(raw, duration_sec, window_sec)


def clamp_positions(
    positions: Sequence[float],
    duration_sec: float,
    window_sec: float = PROBE_WINDOW_SEC,
) -> List[float]:
    """Keep every probe window inside the recording, then drop duplicates.

    A short recording can push two anchors onto the same second; measuring the
    same window twice would look like two agreeing probes and hide that there is
    really only one.
    """
    latest = max(duration_sec - window_sec, 0.0)
    seen: List[float] = []
    for position in positions:
        value = round(min(max(float(position), 0.0), latest), 3)
        if value not in seen:
            seen.append(value)
    return sorted(seen)


def parse_probe_spec(
    spec: Any,
    duration_sec: float,
    window_sec: float = PROBE_WINDOW_SEC,
) -> List[float]:
    """Turn ``--probes`` into probe start times.

    A single number is a *count*; a comma-separated list is the positions
    themselves, in seconds. One number cannot mean both, and a count is the far
    more common request.
    """
    if spec in (None, ""):
        return default_probe_positions(duration_sec, DEFAULT_PROBE_COUNT, window_sec)
    if isinstance(spec, (list, tuple)):
        return clamp_positions([float(item) for item in spec], duration_sec, window_sec)
    text = str(spec).strip()
    if "," in text:
        try:
            values = [float(part) for part in text.split(",") if part.strip()]
        except ValueError:
            raise CalibrationError("--probes positions must be numbers: %s" % text)
        return clamp_positions(values, duration_sec, window_sec)
    try:
        count = int(text)
    except ValueError:
        raise CalibrationError(
            "--probes takes a count (e.g. 4) or positions in seconds "
            "(e.g. 300,1800,4200), not %r" % text
        )
    return default_probe_positions(duration_sec, count, window_sec)


# -- reporting -------------------------------------------------------------

def format_fit(a: float, b: float) -> str:
    """``offset(t)=a+b*t`` as one ASCII line."""
    sign = "-" if b < 0 else "+"
    return "offset(t) = %.4f %s %.9f * t" % (a, sign, abs(b))


def report_lines(report: Mapping[str, Any]) -> List[str]:
    """The lines the command prints: every probe, the span, the fit."""
    lines: List[str] = []
    for probe in report.get("probes", []):
        median = probe.get("median_offset")
        if median is None:
            lines.append(
                "probe at %.1fs: discarded (%d usable points)"
                % (probe.get("at_sec", 0.0), probe.get("n_points", 0))
            )
            continue
        lines.append(
            "probe at %.1fs: median %+.3fs (n=%d, min %+.3f, max %+.3f)"
            % (probe["at_sec"], median, probe["n_points"], probe["min"], probe["max"])
        )
    fit = report.get("fit", {})
    lines.append(
        "span %.3fs%s"
        % (report.get("span", 0.0), " -- drift" if report.get("drift") else " (no drift)")
    )
    lines.append(format_fit(fit.get("a", 0.0), fit.get("b", 0.0)))
    return lines


# -- the command -----------------------------------------------------------

#: Suffix of the untouched original, always written before the calibrated file
#: so that calibrating an SRT in place cannot destroy its own input.
OFFICIAL_SUFFIX = ".official.srt"
REPORT_SUFFIX = ".offset.json"


def cut_probes(
    video: Path,
    positions: Sequence[float],
    workdir: Path,
    window_sec: float = PROBE_WINDOW_SEC,
    extract: Optional[Callable[..., Path]] = None,
) -> List[Tuple[float, Path]]:
    """Cut one audio window per probe. The only part that needs ffmpeg."""
    from lecture2notes.engines import audio as audio_mod

    extract = extract or audio_mod.extract_audio
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    windows: List[Tuple[float, Path]] = []
    for index, position in enumerate(positions):
        target = workdir / ("probe%02d.wav" % index)
        extract(video, target, start_sec=position, duration_sec=window_sec)
        windows.append((float(position), target))
    return windows


def write_outputs(
    stem_path: Path,
    official_cues: Sequence[Cue],
    corrected_cues: Sequence[Cue],
    report: Mapping[str, Any],
) -> Dict[str, Path]:
    """Write the three artefacts, original first.

    Order matters. ``<stem>.srt`` may be the input file itself when the official
    track was already an SRT, so the untouched copy has to be on disk before the
    calibrated one replaces it.
    """
    from lecture2notes.engines.base import write_srt

    stem_path = Path(stem_path)
    official = stem_path.with_name(stem_path.stem + OFFICIAL_SUFFIX)
    calibrated = stem_path.with_name(stem_path.stem + ".srt")
    report_path = stem_path.with_name(stem_path.stem + REPORT_SUFFIX)

    write_srt(official, official_cues)
    write_srt(calibrated, corrected_cues)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {"srt": calibrated, "official": official, "report": report_path}


def run_calibration(
    video: Path,
    subtitle: Path,
    lang: str,
    engine: Any,
    probes: Any = None,
    duration_sec: Optional[float] = None,
    window_sec: float = PROBE_WINDOW_SEC,
    out_dir: Optional[Path] = None,
    workdir: Optional[Path] = None,
    extract: Optional[Callable[..., Path]] = None,
    threshold: float = DRIFT_THRESHOLD,
    min_duration: float = MIN_CUE_DURATION,
) -> Dict[str, Any]:
    """Measure the official track's offset and write a time-shifted copy.

    Nothing is written until the measurement has succeeded: a run that cannot
    place three reliable probes raises :class:`CalibrationError` and leaves the
    directory exactly as it found it, because a half-calibrated subtitle file is
    worse than an uncalibrated one.
    """
    from lecture2notes.engines import audio as audio_mod
    from lecture2notes.engines.base import parse_srt

    video = Path(video)
    subtitle = Path(subtitle)
    official_cues = parse_srt(subtitle)
    if not official_cues:
        raise CalibrationError("no cues could be parsed from %s" % subtitle.name)

    total = duration_sec if duration_sec is not None else audio_mod.media_duration(video)
    if not total:
        # Fall back to the subtitle's own extent: it is never longer than the
        # recording, so probes placed inside it are still inside the video.
        total = official_cues[-1].end
    positions = parse_probe_spec(probes, total, window_sec)
    if len(positions) < MIN_POINTS_PER_PROBE:
        raise CalibrationError(
            "only %d distinct probe positions fit in %.0f seconds of recording; "
            "need %d" % (len(positions), total, MIN_POINTS_PER_PROBE)
        )

    scratch = Path(workdir) if workdir else Path(subtitle).parent / ".l2n-probes"
    windows = cut_probes(video, positions, scratch, window_sec, extract)
    probe_windows = transcribe_probes(engine, windows, lang)

    try:
        corrected, report = calibrate(
            official_cues, probe_windows, min_duration=min_duration, threshold=threshold
        )
    except ValueError as exc:
        raise CalibrationError(str(exc)) from None

    destination = Path(out_dir) / subtitle.name if out_dir else subtitle
    written = write_outputs(destination, official_cues, corrected, report)
    for text in report_lines(report):
        _out.stage("calibrate", text)
    return {"report": report, "files": written, "positions": positions,
            "cues": corrected}


__all__ = [
    "CalibrationError",
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
