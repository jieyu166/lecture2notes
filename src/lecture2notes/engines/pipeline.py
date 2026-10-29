"""The transcribe stage: video in, `<stem>.srt` plus its audit trail out.

New module for this package. It is the glue that the rad-workflow scripts kept
re-inventing per session: extract audio, hand it to an engine, write SRT, apply
the correction table, and leave behind enough evidence to tell what the machine
actually heard.

Three artefacts, and the reason for each:

``<stem>.srt``              what the rest of the pipeline reads.
``<stem>.raw.srt``          what the engine said before any table touched it.
                            Without it a bad correction table is undetectable
                            after the fact, because the evidence was overwritten.
``<stem>.corrections.json`` which rule fired, how often, and from which section.

The raw file and the sidecar are written whenever a table is supplied, even when
no rule fires. "The table ran and matched nothing" and "no table ever ran" are
different facts and must not produce identical directories.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from lecture2notes import _out
from lecture2notes.engines import audio as audio_mod
from lecture2notes.engines import corrections as corrections_mod
from lecture2notes.engines.base import Cue, Engine, write_srt

#: Suffix of the untouched engine output.
RAW_SUFFIX = ".raw.srt"
#: Suffix of the audit sidecar.
SIDECAR_SUFFIX = ".corrections.json"
#: Consecutive windows overlap by this much (at most a tenth of the window), and
#: the seam is cut in the middle of the overlap. A window's first and last
#: seconds are where an engine cuts a sentence in half, repeats a short phrase or
#: pads out fixed-length fragments; with the overlap, every cue that is kept was
#: transcribed with context on both sides.
SEAM_OVERLAP_SEC = 30.0
#: What a one-pass run that ran out of memory says. faster-whisper computes the
#: log-mel features of the whole file in one array: 3.4 hours of audio asked
#: numpy for 3.42 GiB and failed on a machine with 7.8 GB free. Engines that do
#: this declare ``long_audio_sec``, see :func:`auto_chunk_sec`.
MEMORY_HINT = (
    "out of memory transcribing %s in one pass (%s); "
    "split it with --chunk-sec 1800"
)


def seam_overlap(chunk_sec: float) -> float:
    """The overlap :func:`transcribe_audio` uses for windows of ``chunk_sec``."""
    return min(SEAM_OVERLAP_SEC, float(chunk_sec) * 0.1)


def auto_chunk_sec(engine: Engine, duration_sec: Optional[float]) -> Optional[float]:
    """The window an engine wants for a recording this long, or None."""
    limit = getattr(engine, "long_audio_sec", None)
    window = getattr(engine, "long_audio_chunk_sec", None)
    if not limit or not window or not duration_sec or duration_sec <= limit:
        return None
    return float(window)


@dataclass
class TranscribeResult:
    """Everything the transcribe stage produced, for the CLI to report on."""

    srt: Optional[Path] = None
    raw: Optional[Path] = None
    sidecar: Optional[Path] = None
    cues: List[Cue] = field(default_factory=list)
    engine: str = ""
    lang: str = ""
    skipped: bool = False
    corrections: List[Dict[str, Any]] = field(default_factory=list)

    def outputs(self) -> List[Path]:
        return [p for p in (self.srt, self.raw, self.sidecar) if p is not None]


def shift_cues(cues: Sequence[Cue], offset_sec: float) -> List[Cue]:
    """Move a window's cues onto the recording's own clock."""
    return [
        Cue(start=cue.start + offset_sec, end=cue.end + offset_sec, text=cue.text)
        for cue in cues
    ]


def transcribe_audio(
    engine: Engine,
    source: Path,
    lang: str,
    workdir: Path,
    chunk_sec: Optional[float] = None,
    duration_sec: Optional[float] = None,
    extract: Optional[Callable[..., Path]] = None,
    overlap_sec: Optional[float] = None,
) -> List[Cue]:
    """Run the engine over ``source``, chunking the audio when asked to.

    ``chunk_sec`` exists for backends that cannot take an arbitrarily long input.
    An engine that declares ``long_audio_sec`` gets chunked automatically past
    that length. Windows overlap by ``overlap_sec`` (default
    :func:`seam_overlap`); each keeps only the cues that start on its side of
    the middle of an overlap, and the times are shifted back onto the
    recording's clock, so the caller never sees the seam.
    """
    extract = extract or audio_mod.extract_audio
    source = Path(source)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    if not chunk_sec and getattr(engine, "long_audio_sec", None):
        if duration_sec is None:
            try:
                duration_sec = audio_mod.media_duration(source)
            except Exception:  # noqa: BLE001 - unknown length: one pass, as before
                duration_sec = None
        chunk_sec = auto_chunk_sec(engine, duration_sec)
        if chunk_sec:
            _out.stage(
                "transcribe", "%.0f min recording: windows of %.0f min"
                % (duration_sec / 60, chunk_sec / 60)
            )

    if not chunk_sec:
        wav = source
        if audio_mod.needs_extraction(source):
            wav = extract(source, workdir / (source.stem + ".wav"))
        try:
            return list(engine.transcribe(Path(wav), lang))
        except MemoryError as exc:  # numpy's _ArrayMemoryError is one
            raise RuntimeError(MEMORY_HINT % (source.name, exc)) from exc

    total = duration_sec or audio_mod.media_duration(source)
    overlap = seam_overlap(chunk_sec) if overlap_sec is None else max(float(overlap_sec), 0.0)
    starts = audio_mod.plan_chunks(total, float(chunk_sec), overlap)
    progress = _out.Progress("transcribe", len(starts))
    cues: List[Cue] = []
    for index, start in enumerate(starts):
        window = workdir / ("%s-chunk%03d.wav" % (source.stem, index))
        extract(source, window, start_sec=start, duration_sec=float(chunk_sec))
        low = starts[index] + overlap / 2.0 if index else float("-inf")
        high = starts[index + 1] + overlap / 2.0 if index + 1 < len(starts) else float("inf")
        cues.extend(
            cue for cue in shift_cues(engine.transcribe(window, lang), start)
            if low <= cue.start < high
        )
        try:
            window.unlink()
        except OSError as exc:  # pragma: no cover - a locked temp file is not fatal
            _out.say("warn", "could not remove temp file %s: %s" % (window, exc))
        progress.advance()
    progress.finish()
    return trim_seam_overlaps(cues)


def trim_seam_overlaps(cues: Sequence[Cue]) -> List[Cue]:
    """Make a stitched cue list monotonic: no cue starts before the last one ends.

    Windows are transcribed independently, so a window's last cue can end after
    the next window's first cue starts -- most easily when that last cue was a
    zero-length one the engine widened. `check transcribe` reports both an
    overlap and a zero-length cue as errors.

    The later cue yields: its start moves to where the kept one ends, and it is
    dropped when that leaves it nothing at all. The cue already on the list is
    never shortened, so it keeps the span the aligner gave it.
    """
    kept: List[Cue] = []
    for cue in cues:
        start = kept[-1].end if kept and cue.start < kept[-1].end else cue.start
        if cue.end > start:
            kept.append(Cue(start=round(start, 3), end=cue.end, text=cue.text))
    return kept


def apply_table(
    srt_path: Path,
    pairs: Sequence[corrections_mod.Pair],
    cues: Sequence[Cue],
    table_path: Optional[str] = None,
    s2t: bool = True,
    s2t_config: str = corrections_mod.S2T_CONFIG,
) -> Dict[str, Any]:
    """Write the raw copy, correct the SRT in place and write the sidecar.

    The raw copy is written from the cue objects rather than copied from disk, so
    it is the engine's output by construction and cannot be a corrected file that
    was mistakenly copied.
    """
    srt_path = Path(srt_path)
    raw_path = srt_path.parent / (srt_path.stem + RAW_SUFFIX)
    write_srt(raw_path, cues)
    result = corrections_mod.correct_file(
        srt_path,
        pairs,
        s2t=s2t,
        s2t_config=s2t_config,
        backup=False,  # the raw copy above already is the backup
        sidecar=True,
        table_path=table_path,
        always=True,
    )
    result["raw"] = str(raw_path)
    return result


def transcribe_video(
    video: Path,
    lang: str,
    engine: Engine,
    out_dir: Optional[Path] = None,
    pairs: Optional[Sequence[corrections_mod.Pair]] = None,
    table_path: Optional[str] = None,
    s2t: bool = True,
    s2t_config: str = corrections_mod.S2T_CONFIG,
    force: bool = False,
    chunk_sec: Optional[float] = None,
    workdir: Optional[Path] = None,
    extract: Optional[Callable[..., Path]] = None,
) -> TranscribeResult:
    """Transcribe one recording and leave the stage's three artefacts behind.

    The engine's dependency check runs before any file is created, so a missing
    runtime exits 3 without littering the output directory.
    """
    video = Path(video)
    destination_dir = Path(out_dir) if out_dir else video.parent
    srt_path = destination_dir / (video.stem + ".srt")
    result = TranscribeResult(engine=engine.name, lang=lang)

    if srt_path.exists() and not force:
        _out.stage("transcribe", "skip (exists): %s" % srt_path.name)
        result.srt = srt_path
        result.skipped = True
        return result

    engine.check()

    if workdir is not None:
        cues = transcribe_audio(
            engine, video, lang, Path(workdir), chunk_sec=chunk_sec, extract=extract
        )
    else:
        with tempfile.TemporaryDirectory(prefix="l2n-audio-") as scratch:
            cues = transcribe_audio(
                engine, video, lang, Path(scratch), chunk_sec=chunk_sec, extract=extract
            )

    if not cues:
        raise RuntimeError("%s produced no cues for %s" % (engine.name, video.name))

    destination_dir.mkdir(parents=True, exist_ok=True)
    write_srt(srt_path, cues)
    result.srt = srt_path
    result.cues = cues
    _out.stage("transcribe", "%d cues -> %s" % (len(cues), srt_path.name))

    # Conversion to traditional Chinese is a separate promise from the
    # correction table: ``--no-s2t`` documents it as on by default, so a run
    # with no ``--corrections`` still owes the user that pass. Gating the whole
    # block on ``pairs is not None`` skipped it silently, which went unnoticed
    # while every engine happened to emit traditional Chinese already; qwen3_asr
    # emits simplified, so the gap reached the subtitles.
    if pairs is not None or s2t:
        applied = apply_table(
            srt_path, pairs or [], cues, table_path=table_path, s2t=s2t,
            s2t_config=s2t_config,
        )
        result.raw = Path(applied["raw"])
        result.sidecar = Path(applied["sidecar"]) if applied.get("sidecar") else None
        result.corrections = list(applied.get("hits") or [])
    return result


__all__ = [
    "MEMORY_HINT",
    "RAW_SUFFIX",
    "SEAM_OVERLAP_SEC",
    "SIDECAR_SUFFIX",
    "TranscribeResult",
    "apply_table",
    "auto_chunk_sec",
    "seam_overlap",
    "shift_cues",
    "transcribe_audio",
    "transcribe_video",
]
