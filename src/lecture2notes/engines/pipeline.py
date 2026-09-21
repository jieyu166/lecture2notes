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
) -> List[Cue]:
    """Run the engine over ``source``, chunking the audio when asked to.

    ``chunk_sec`` exists for backends that cannot take an arbitrarily long input.
    Chunks are transcribed independently and their cue times shifted back onto
    the recording's clock, so the caller never sees the seam.
    """
    extract = extract or audio_mod.extract_audio
    source = Path(source)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    if not chunk_sec:
        wav = source
        if audio_mod.needs_extraction(source):
            wav = extract(source, workdir / (source.stem + ".wav"))
        return list(engine.transcribe(Path(wav), lang))

    total = duration_sec or audio_mod.media_duration(source)
    starts = audio_mod.plan_chunks(total, float(chunk_sec))
    progress = _out.Progress("transcribe", len(starts))
    cues: List[Cue] = []
    for index, start in enumerate(starts):
        window = workdir / ("%s-chunk%03d.wav" % (source.stem, index))
        extract(source, window, start_sec=start, duration_sec=float(chunk_sec))
        cues.extend(shift_cues(engine.transcribe(window, lang), start))
        try:
            window.unlink()
        except OSError:  # pragma: no cover - a locked temp file is not fatal
            pass
        progress.advance()
    progress.finish()
    return cues


def apply_table(
    srt_path: Path,
    pairs: Sequence[corrections_mod.Pair],
    cues: Sequence[Cue],
    table_path: Optional[str] = None,
    s2t: bool = True,
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

    if pairs is not None:
        applied = apply_table(srt_path, pairs, cues, table_path=table_path, s2t=s2t)
        result.raw = Path(applied["raw"])
        result.sidecar = Path(applied["sidecar"]) if applied.get("sidecar") else None
        result.corrections = list(applied.get("hits") or [])
    return result


__all__ = [
    "RAW_SUFFIX",
    "SIDECAR_SUFFIX",
    "TranscribeResult",
    "apply_table",
    "shift_cues",
    "transcribe_audio",
    "transcribe_video",
]
