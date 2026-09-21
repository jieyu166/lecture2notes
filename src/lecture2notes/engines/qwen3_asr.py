"""Qwen3-ASR backend: open weights, local inference, native timestamps.

New module for this package; there is no rad-workflow script to port. It follows
the same Engine contract as the ported backends so the registry treats all four
the same way.

Qwen3-ASR ships open weights and a local inference package, so it stays inside
the privacy boundary: nothing leaves the machine except the first weight
download, which ``--model-dir`` lets the user do beforehand. The model can guess
the language, but ``--lang`` is still mandatory: a wrong guess produces a
fluent, plausible and entirely invented transcript.

Inference itself lands with the transcription group; this module only pins the
metadata and makes the missing-dependency path exact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from lecture2notes import _deps
from lecture2notes.engines.base import Cue, Engine, EngineMeta

DEFAULT_MODEL = "Qwen/Qwen3-ASR-0.6B"
#: The larger checkpoint, selected with --model.
LARGE_MODEL = "Qwen/Qwen3-ASR-1.7B"
#: transformers is the default backend; vLLM is opt-in.
BACKENDS = ("transformers", "vllm")


class Qwen3AsrEngine(Engine):
    """Local Qwen3-ASR inference through the ``qwen-asr`` package."""

    meta = EngineMeta(
        name="qwen3_asr",
        local=True,
        needs_gpu=True,
        has_timestamps=True,
        default_model=DEFAULT_MODEL,
        extra="open weights, transformers backend by default",
    )

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        model_dir: Optional[Path] = None,
        backend: str = "transformers",
        **options: Any,
    ) -> None:
        super().__init__(**options)
        if backend not in BACKENDS:
            raise ValueError("unknown qwen3_asr backend: %s" % backend)
        self.model = model
        self.model_dir = Path(model_dir) if model_dir else None
        self.backend = backend

    def model_reference(self) -> str:
        """A local directory wins over the hub name, so nothing is downloaded."""
        return str(self.model_dir) if self.model_dir else self.model

    def check(self) -> None:
        _deps.require_qwen_asr()

    def transcribe(self, audio: Path, lang: str) -> List[Cue]:
        self.check()
        raise NotImplementedError(
            "qwen3_asr inference is not wired up yet; use --engine faster_whisper"
        )


__all__ = ["BACKENDS", "DEFAULT_MODEL", "LARGE_MODEL", "Qwen3AsrEngine"]
