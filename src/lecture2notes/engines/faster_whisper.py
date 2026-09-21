"""faster-whisper backend: official Whisper weights, zero setup.

Ported from rad-workflow skills/whisper-srt-zh/scripts/transcribe.py
(``run_faster_whisper``).
Inspired by drpwchen/lecture-to-notes scripts/transcribe_video.py @79053a3

VAD and ``condition_on_previous_text`` are off by default. VAD quietly eats the
quiet speech at the edge of a pause, and conditioning propagates a misheard word
forward into the cues that follow it. Both defaults come from measurement, not
from taste.

``device="auto"`` means "whatever works here", and this engine's own metadata
says ``needs_gpu=False``. So when CTranslate2 picks the GPU and then cannot load
its CUDA libraries -- the ordinary state of a machine that has an NVIDIA card
but no cuBLAS, and of most CI runners -- the load is retried on the CPU instead
of failing the run. An explicitly requested device is never second-guessed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from lecture2notes import _deps, _out
from lecture2notes.engines.base import Cue, DependencyStatus, Engine, EngineMeta

DEFAULT_MODEL = "large-v3"

#: CTranslate2 picks the device itself under this name.
AUTO_DEVICE = "auto"

#: Where an unusable GPU falls back to.
CPU_DEVICE = "cpu"

#: The default compute type, which is a GPU format.
DEFAULT_COMPUTE_TYPE = "float16"

#: What ``float16`` becomes once the model is on the CPU. CTranslate2 accepts
#: float16 there, converts the weights and warns on every single run; int8 is
#: the type a CPU actually wants, so the default is translated rather than
#: passed through and apologised for.
CPU_COMPUTE_TYPE = "int8"


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
        device: str = AUTO_DEVICE,
        compute_type: str = DEFAULT_COMPUTE_TYPE,
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

    # -- model loading ---------------------------------------------------
    def compute_type_for(self, device: str) -> str:
        """The compute type to use on *device*.

        Only the default is translated. A compute type the caller named is
        theirs, including a slow one: being overruled without being told is
        worse than being slow.
        """
        if device == CPU_DEVICE and self.compute_type == DEFAULT_COMPUTE_TYPE:
            return CPU_COMPUTE_TYPE
        return self.compute_type

    def run_on(self, module, device: str, audio: Path, lang: str) -> List[Cue]:
        """Load the model on *device* and drain a whole transcription from it."""
        whisper_model = module.WhisperModel(
            self._model_reference(),
            device=device,
            compute_type=self.compute_type_for(device),
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

    # -- inference -------------------------------------------------------
    def transcribe(self, audio: Path, lang: str) -> List[Cue]:
        """Transcribe, retrying on the CPU when ``auto`` picked an unusable GPU.

        The whole run is retried, not just the model load: CTranslate2 loads the
        weights without touching cuBLAS and only fails on the first encode, so a
        fallback that wrapped the constructor alone would catch nothing and the
        run would still die on a machine this engine claims to support.
        """
        self.check()
        module = _deps.require_module("faster_whisper")
        try:
            return self.run_on(module, self.device, audio, lang)
        except Exception as exc:  # noqa: BLE001 - the runtime raises many types
            if self.device != AUTO_DEVICE:
                raise
            _out.warn(
                "faster_whisper: device %s could not be used (%s); "
                "retrying on the CPU" % (AUTO_DEVICE, exc)
            )
        return self.run_on(module, CPU_DEVICE, audio, lang)


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


__all__ = [
    "AUTO_DEVICE",
    "CPU_DEVICE",
    "CPU_COMPUTE_TYPE",
    "DEFAULT_COMPUTE_TYPE",
    "DEFAULT_MODEL",
    "FasterWhisperEngine",
]
