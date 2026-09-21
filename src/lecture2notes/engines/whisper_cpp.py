"""whisper.cpp backend: call an external binary, no Python model stack.

Ported from rad-workflow skills/whisper-srt-zh/scripts/transcribe.py
(the ``--engine whisper.cpp`` subprocess branch).

This engine exists for machines without a CUDA Python stack. It is much faster
than Breeze but noticeably worse on domain terms, so it is never the default.
The binary and the ggml model file are supplied by the user; nothing is
downloaded.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, List, Optional

from lecture2notes import _deps
from lecture2notes.engines.base import Cue, Engine, EngineMeta, parse_srt

#: Environment variables that point at a user-supplied binary and model.
BIN_ENV = "LECTURE2NOTES_WHISPER_CPP_BIN"
MODEL_ENV = "LECTURE2NOTES_WHISPER_CPP_MODEL"


class WhisperCppEngine(Engine):
    """Run a whisper.cpp executable and read back the SRT it writes."""

    meta = EngineMeta(
        name="whisper_cpp",
        local=True,
        needs_gpu=False,
        has_timestamps=True,
        default_model="ggml-large-v3-turbo.bin",
        extra="bring your own binary and ggml model",
    )

    def __init__(
        self,
        binary: Optional[str] = None,
        model: Optional[str] = None,
        **options: Any,
    ) -> None:
        super().__init__(**options)
        self.binary = binary or os.environ.get(BIN_ENV) or "whisper-cli"
        self.model = model or os.environ.get(MODEL_ENV)

    def check(self) -> None:
        candidate = Path(self.binary)
        if candidate.is_file():
            resolved = str(candidate)
        else:
            resolved = _deps.require_binary(self.binary, _deps.INSTALL_HINTS["whisper_cpp"])
        if not self.model or not Path(self.model).is_file():
            raise _deps.MissingDependency(
                "whisper_cpp_model",
                "pass --whisper-cpp-model <path to ggml model> or set %s" % MODEL_ENV,
                "model not found: %s" % self.model,
            )
        return resolved

    def build_command(self, audio: Path, lang: str, out_stem: Path) -> List[str]:
        """The exact argv used, exposed so a test can assert it without running it."""
        return [
            str(self.binary),
            "-m", str(self.model),
            "-l", str(lang),
            "-osrt",
            "-of", str(out_stem),
            "-f", str(audio),
        ]

    def transcribe(self, audio: Path, lang: str) -> List[Cue]:
        self.check()
        audio = Path(audio)
        out_stem = audio.with_suffix("")
        subprocess.run(self.build_command(audio, lang, out_stem), check=False)
        produced = out_stem.with_suffix(".srt")
        if not produced.is_file():
            raise RuntimeError("whisper.cpp produced no subtitle file: %s" % produced)
        return parse_srt(produced)


__all__ = ["BIN_ENV", "MODEL_ENV", "WhisperCppEngine"]
