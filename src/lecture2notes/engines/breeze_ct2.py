"""Breeze-ASR-25 through CTranslate2: the default engine for Mandarin lectures.

Ported from rad-workflow skills/whisper-srt-zh/scripts/transcribe.py
(``run_faster_whisper`` plus the CT2 model-directory checks).
Inspired by drpwchen/lecture-to-notes scripts/transcribe_video.py @79053a3

Breeze keeps domain terminology intact where a faster general model does not, so
it is the default even though it costs more time per minute of audio. The
weights are not shipped: run ``l2n convert-model`` once to build the CT2
directory, then point ``--model-dir`` at it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from lecture2notes import _deps
from lecture2notes.engines.base import EngineMeta
from lecture2notes.engines.faster_whisper import FasterWhisperEngine

#: Source weights that ``l2n convert-model`` converts.
SOURCE_MODEL = "MediaTek-Research/Breeze-ASR-25"
#: Environment variable that overrides the converted-model directory.
MODEL_DIR_ENV = "LECTURE2NOTES_CT2_MODEL"


def default_model_dir() -> Path:
    """Where ``l2n convert-model`` puts the converted weights by default."""
    override = os.environ.get(MODEL_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / "AppData" / "Local" / "whisper-models" / "breeze-asr-25-ct2"


class BreezeCT2Engine(FasterWhisperEngine):
    """faster-whisper driven from a local CTranslate2 model directory."""

    meta = EngineMeta(
        name="breeze_ct2",
        local=True,
        needs_gpu=True,
        has_timestamps=True,
        default_model=SOURCE_MODEL,
        extra="requires `l2n convert-model` once",
    )

    def __init__(self, model_dir: Optional[Path] = None, **options: Any) -> None:
        options.setdefault("device", "cuda")
        super().__init__(**options)
        self.model_dir = Path(model_dir) if model_dir else default_model_dir()

    def check(self) -> None:
        _deps.require_module("faster_whisper")
        _deps.require_ct2_model(self.model_dir)

    def _model_reference(self) -> str:
        return str(self.model_dir)


__all__ = ["MODEL_DIR_ENV", "SOURCE_MODEL", "BreezeCT2Engine", "default_model_dir"]
