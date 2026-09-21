"""faster-whisper backend: official Whisper weights, zero setup.

Ported from rad-workflow skills/whisper-srt-zh/scripts/transcribe.py
(``run_faster_whisper``).
Inspired by drpwchen/lecture-to-notes scripts/transcribe_video.py @79053a3

VAD and ``condition_on_previous_text`` are off by default. VAD quietly eats the
quiet speech at the edge of a pause, and conditioning propagates a misheard word
forward into the cues that follow it. Both defaults come from measurement, not
from taste.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from lecture2notes import _deps, _out
from lecture2notes.engines.base import Cue, DependencyStatus, Engine, EngineMeta

DEFAULT_MODEL = "large-v3"


class FasterWhisperEngine(Engine):
    """Whisper inference through CTranslate2, using an official model name."""

    meta = EngineMeta(
        name="faster_whisper",
        local=True,
        needs_gpu=False,
        has_timestamps=True,
        default_model=DEFAULT_MODEL,
        extra="runs on CPU, faster with CUDA",
    )

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        device: str = "auto",
        compute_type: str = "float16",
        beam_size: int = 5,
        vad: bool = False,
        condition: bool = False,
        **options: Any,
    ) -> None:
        super().__init__(**options)
        self.model = model
        self.device = device
        self.compute_type = compute_type
        self.beam_size = int(beam_size)
        self.vad = bool(vad)
        self.condition = bool(condition)

    # -- dependency ------------------------------------------------------
    def check(self) -> None:
        _deps.require_module("faster_whisper")

    def probe(self) -> DependencyStatus:
        if _deps.module_available("faster_whisper"):
            return DependencyStatus(self.meta.name, True, "model %s" % self.model)
        return DependencyStatus(
            self.meta.name, False, "faster_whisper (%s)" % _deps.INSTALL_HINTS["faster_whisper"]
        )

    def _model_reference(self) -> str:
        return self.model

    # -- inference -------------------------------------------------------
    def transcribe(self, audio: Path, lang: str) -> List[Cue]:
        self.check()
        module = _deps.require_module("faster_whisper")
        whisper_model = module.WhisperModel(
            self._model_reference(),
            device=self.device,
            compute_type=self.compute_type,
        )
        segments, info = whisper_model.transcribe(
            str(audio),
            language=None if str(lang).lower() == "auto" else lang,
            beam_size=self.beam_size,
            vad_filter=self.vad,
            condition_on_previous_text=self.condition,
        )
        cues = _collect(segments, self.meta.name)
        language = getattr(info, "language", None)
        probability = getattr(info, "language_probability", None)
        if language:
            detail = "detected language %s" % language
            if isinstance(probability, (int, float)):
                detail += " (p=%.2f)" % probability
            _out.stage(self.meta.name, "%s, %d cues" % (detail, len(cues)))
        return cues


def _collect(segments, stage_name: str) -> List[Cue]:
    """Drain a faster-whisper generator into cues, reporting progress as it goes.

    The generator is lazy and the total is unknown, so progress is reported by
    media position rather than by a percentage.
    """
    cues: List[Cue] = []
    for segment in segments:
        text = (getattr(segment, "text", "") or "").strip()
        if not text:
            continue
        cues.append(Cue(start=float(segment.start), end=float(segment.end), text=text))
        if len(cues) % 50 == 0:
            _out.stage(stage_name, "%d cues, at %.1f min" % (len(cues), float(segment.end) / 60))
    return cues


__all__ = ["DEFAULT_MODEL", "FasterWhisperEngine"]
