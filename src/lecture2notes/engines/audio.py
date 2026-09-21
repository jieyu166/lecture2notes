"""Pull audio out of a video for the ASR engines, and ask how long it is.

New module for this package. The rad-workflow scripts each shelled out to their
own ffmpeg line with slightly different arguments; a single audio contract
avoids a transcript and a calibration probe being fed differently shaped audio.

The contract is 16 kHz mono PCM WAV, because every engine here either wants
exactly that (whisper.cpp, faster-whisper) or resamples to it internally.

Every function that runs a subprocess takes its runner as an argument, so tests
exercise the argv without ffmpeg installed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

from lecture2notes import _deps, _out

#: Sample rate and channel count every engine in this package expects.
SAMPLE_RATE = 16000
CHANNELS = 1

#: Extensions treated as "already audio": no extraction pass is needed for a WAV
#: that already matches the contract, but anything else is re-encoded, because a
#: 44.1 kHz stereo MP3 makes some backends silently resample badly.
READY_SUFFIXES = (".wav",)

Runner = Callable[[Sequence[str]], Any]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def build_extract_command(
    ffmpeg: str,
    source: Path,
    destination: Path,
    start_sec: Optional[float] = None,
    duration_sec: Optional[float] = None,
) -> List[str]:
    """The exact argv used, exposed so a test can assert it without running it.

    ``-ss`` is placed before ``-i``: seeking on the input is the fast path, and
    for a probe window a few milliseconds of seek inaccuracy is irrelevant next
    to a 90 second window.
    """
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if start_sec is not None:
        argv += ["-ss", "%.3f" % float(start_sec)]
    argv += ["-i", str(source)]
    if duration_sec is not None:
        argv += ["-t", "%.3f" % float(duration_sec)]
    argv += [
        "-vn",
        "-ac", str(CHANNELS),
        "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        str(destination),
    ]
    return argv


def extract_audio(
    source: Path,
    destination: Path,
    start_sec: Optional[float] = None,
    duration_sec: Optional[float] = None,
    runner: Runner = _run,
) -> Path:
    """Write 16 kHz mono WAV from ``source``, optionally only one window of it."""
    ffmpeg = _deps.require_ffmpeg()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    argv = build_extract_command(ffmpeg, Path(source), destination, start_sec, duration_sec)
    result = runner(argv)
    code = getattr(result, "returncode", 0)
    if code:
        detail = (getattr(result, "stderr", "") or "").strip().splitlines()
        raise RuntimeError(
            "ffmpeg failed (exit %s) extracting audio from %s%s"
            % (code, Path(source).name, (": " + detail[-1]) if detail else "")
        )
    if not destination.is_file():
        raise RuntimeError("ffmpeg wrote no audio file: %s" % destination)
    return destination


def needs_extraction(source: Path) -> bool:
    return Path(source).suffix.lower() not in READY_SUFFIXES


def media_duration(source: Path, runner: Runner = _run) -> float:
    """Duration in seconds, or 0.0 when ffprobe cannot tell.

    Returning 0.0 rather than raising is deliberate: a caller that only wants to
    place probe windows can fall back to a fixed layout, and a caller that truly
    needs the number checks for zero itself.
    """
    ffprobe = _deps.require_ffprobe()
    result = runner([
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(source),
    ])
    try:
        return float((getattr(result, "stdout", "") or "").strip())
    except (TypeError, ValueError):
        _out.warn("ffprobe could not read the duration of %s" % Path(source).name)
        return 0.0


#: A final window shorter than this is dropped rather than transcribed. The
#: window before it already reaches the end of the recording, so the only thing
#: lost is a sliver of trailing audio -- and feeding that sliver to a model is
#: not a lesser evil: Qwen3-ASR raises "Padding size should be less than the
#: corresponding input dimension" on a 33 ms window and takes the whole run down
#: with it at the very last step.
MIN_TAIL_SEC = 1.0


def plan_chunks(duration_sec: float, chunk_sec: float, overlap_sec: float = 0.0) -> List[float]:
    """Start times covering ``duration_sec`` in windows of ``chunk_sec``.

    Some backends cannot process an arbitrarily long recording in one pass, so a
    long lecture is transcribed window by window and the cue times are shifted
    back afterwards.

    A duration a hair over an exact multiple of ``chunk_sec`` is the normal case,
    not a corner case: container durations carry milliseconds, so a 6-minute clip
    probed at 360.033 s used to plan a fourth 33 ms window at 360 s. Any window
    shorter than :data:`MIN_TAIL_SEC` is therefore dropped.
    """
    if chunk_sec <= 0:
        raise ValueError("chunk_sec must be positive")
    step = max(chunk_sec - max(overlap_sec, 0.0), 1.0)
    if duration_sec <= chunk_sec:
        return [0.0]
    starts: List[float] = []
    position = 0.0
    while position < duration_sec:
        # Never drop the first window: a recording shorter than MIN_TAIL_SEC
        # still has to be transcribed, and the guard above already returned for
        # anything that fits in one window.
        if starts and duration_sec - position < MIN_TAIL_SEC:
            break
        starts.append(round(position, 3))
        position += step
    return starts


__all__ = [
    "CHANNELS",
    "MIN_TAIL_SEC",
    "READY_SUFFIXES",
    "SAMPLE_RATE",
    "build_extract_command",
    "extract_audio",
    "media_duration",
    "needs_extraction",
    "plan_chunks",
]
