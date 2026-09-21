"""Qwen3-ASR backend: open weights, local inference, aligner-derived timestamps.

New module for this package; there is no rad-workflow script to port. It follows
the same Engine contract as the ported backends so the registry treats all four
the same way.

Qwen3-ASR ships open weights (Apache 2.0) and a first-party inference package, so
it stays inside the privacy boundary: nothing leaves the machine except the
initial weight download, which ``--model-dir`` lets the user do beforehand. The
model can guess the language, but ``--lang`` is still mandatory: a wrong guess
produces a fluent, plausible and entirely invented transcript.

Three facts about the real API shaped this module, all verified against the
upstream source on 2026-09-21 (links in README's engine table):

1. **The ASR model does not emit timestamps.** ``Qwen3ASRModel.transcribe``
   returns ``language`` and ``text`` only. Cue times come from a separate
   first-party checkpoint, ``Qwen/Qwen3-ForcedAligner-0.6B``, passed as
   ``forced_aligner=``; no external CTC aligner is involved. That is another
   1.84 GB of weights on top of the ASR model, which is why the aligner has its
   own ``--aligner-dir``.
2. **Alignment is per token, not per cue.** For Chinese the aligner returns one
   item per character. Subtitles built one character at a time are useless, so
   :func:`cues_from_alignment` groups items back into readable cues on sentence
   punctuation, a silence gap, a length cap or a duration cap.
3. **The aligner is documented for up to five minutes of speech.** A lecture is
   an hour or three, so this engine declares :attr:`chunk_sec`; the transcribe
   stage cuts the audio into windows of that length and shifts each window's
   cues back onto the recording's clock. The window is 120 s rather than a value
   just under the documented ceiling because a 240 s window measurably loses
   speech -- see :data:`DEFAULT_CHUNK_SEC`.

``start_time`` and ``end_time`` on an alignment item are **seconds**, despite
being annotated ``int`` upstream: the aligner divides its internal milliseconds
by 1000 before returning them. Reading them as milliseconds would put every cue
three orders of magnitude too late, so they are taken as float seconds here and
nothing converts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from lecture2notes import _deps, _out
from lecture2notes.engines.base import Cue, DependencyStatus, Engine, EngineMeta

DEFAULT_MODEL = "Qwen/Qwen3-ASR-0.6B"
#: The larger checkpoint, selected with --model.
LARGE_MODEL = "Qwen/Qwen3-ASR-1.7B"
#: Timestamps come from this separate first-party checkpoint, not from the ASR
#: model. Without it ``transcribe`` can only return one untimed block of text.
DEFAULT_ALIGNER = "Qwen/Qwen3-ForcedAligner-0.6B"
#: transformers is the default backend; vLLM is opt-in and needs qwen-asr[vllm].
BACKENDS = ("transformers", "vllm")

#: Upstream takes a language *name*, not an ISO code, and ``None`` means
#: "detect". Chinese, English and Cantonese appear verbatim in the upstream
#: documentation; Japanese follows the same naming convention but was not found
#: spelled out, so it is marked here rather than presented as verified.
LANGUAGE_NAMES: Dict[str, Optional[str]] = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",  # convention, not quoted upstream
    "yue": "Cantonese",
    "auto": None,
}

#: The aligner is documented for up to five minutes of speech, but staying just
#: under that documented ceiling turned out not to be enough. Measured on
#: 2026-09-21 against a six-minute Mandarin lecture (RTX 4060 Laptop 8 GB, 0.6B
#: plus aligner, transformers backend): at 240 s the run reproducibly dropped a
#: continuous 23.5 s stretch of speech out of the middle of the first window --
#: 1767 transcribed characters against 1902 at 120 s, with the missing span
#: confirmed present in a Breeze-ASR-25 transcript of the same audio. At 120 s
#: nothing was lost and wall time was comparable, so the shorter window is the
#: default; ``--chunk-sec`` raises it for anyone who would rather have fewer
#: model calls than every sentence.
DEFAULT_CHUNK_SEC = 120.0

#: Cue assembly limits. A cue ends at sentence punctuation, or when one of these
#: is reached, whichever comes first.
MAX_CUE_CHARS = 32
MAX_CUE_SEC = 8.0
#: A silence longer than this is a cue boundary regardless of punctuation.
CUE_GAP_SEC = 0.8
SENTENCE_END = "。！？…!?.;；"


def language_name(lang: str) -> Optional[str]:
    """Map the CLI's ISO code onto the language name upstream expects.

    An unknown code passes through capitalised rather than raising: a language
    added upstream should not need a release here.
    """
    key = str(lang).lower()
    if key in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[key]
    return key.capitalize()


def alignment_items(result: Any) -> List[Any]:
    """Pull the item list out of a transcription result, or return empty.

    Written defensively on purpose: this is the one place where the shape of a
    third-party return value is inspected, and a shape change upstream should
    degrade to "no timestamps" rather than raise an AttributeError from inside
    a cue loop.
    """
    stamps = getattr(result, "time_stamps", None)
    if stamps is None:
        return []
    if isinstance(stamps, dict):
        return list(stamps.get("items") or [])
    if isinstance(stamps, (list, tuple)):
        return list(stamps)
    return list(getattr(stamps, "items", None) or [])


def _item_fields(item: Any) -> Optional[tuple]:
    """``(text, start_sec, end_sec)`` from an item object or mapping."""
    if isinstance(item, dict):
        text, start, end = item.get("text"), item.get("start_time"), item.get("end_time")
    else:
        text = getattr(item, "text", None)
        start = getattr(item, "start_time", None)
        end = getattr(item, "end_time", None)
    if text is None or start is None or end is None:
        return None
    try:
        return str(text), float(start), float(end)
    except (TypeError, ValueError):
        return None


def _needs_space(previous: str, current: str) -> bool:
    """Latin words need a separator; CJK characters must not get one."""
    if not previous or not current:
        return False
    return previous[-1].isascii() and previous[-1].isalnum() and current[0].isascii()


def cues_from_alignment(
    items: Sequence[Any],
    max_chars: int = MAX_CUE_CHARS,
    max_sec: float = MAX_CUE_SEC,
    gap_sec: float = CUE_GAP_SEC,
) -> List[Cue]:
    """Group per-token alignment items into readable subtitle cues.

    The aligner returns one item per character for Chinese and per word for
    space-separated languages, so the joining rule has to work for both: items
    are concatenated as-is, which is correct for CJK, and a space is inserted
    only between two items that both look like Latin words.
    """
    cues: List[Cue] = []
    parts: List[str] = []
    start: Optional[float] = None
    end: Optional[float] = None

    def flush() -> None:
        nonlocal parts, start, end
        body = "".join(parts).strip()
        if body and start is not None and end is not None:
            cues.append(Cue(start=round(start, 3), end=round(max(end, start), 3), text=body))
        parts, start, end = [], None, None

    for item in items:
        fields = _item_fields(item)
        if fields is None:
            continue
        token, item_start, item_end = fields
        if not token.strip():
            continue
        if start is not None and end is not None and (item_start - end) >= gap_sec:
            flush()
        if start is None:
            start = item_start
        if parts and _needs_space(parts[-1], token):
            parts.append(" ")
        parts.append(token)
        end = item_end
        body = "".join(parts).strip()
        if token[-1] in SENTENCE_END or len(body) >= max_chars or (end - start) >= max_sec:
            flush()
    flush()
    return cues


class Qwen3AsrEngine(Engine):
    """Local Qwen3-ASR inference through the first-party ``qwen-asr`` package."""

    meta = EngineMeta(
        name="qwen3_asr",
        local=True,
        needs_gpu=True,
        has_timestamps=True,
        default_model=DEFAULT_MODEL,
        extra="open weights; timestamps need the forced aligner (+1.84 GB)",
    )

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        model_dir: Optional[Path] = None,
        aligner: str = DEFAULT_ALIGNER,
        aligner_dir: Optional[Path] = None,
        backend: str = "transformers",
        dtype: str = "bfloat16",
        device: str = "cuda:0",
        context: str = "",
        max_new_tokens: int = 512,
        chunk_sec: float = DEFAULT_CHUNK_SEC,
        **options: Any,
    ) -> None:
        super().__init__(**options)
        if backend not in BACKENDS:
            raise ValueError(
                "unknown qwen3_asr backend: %s (known: %s)" % (backend, ", ".join(BACKENDS))
            )
        self.model = model
        self.model_dir = Path(model_dir) if model_dir else None
        self.aligner = aligner
        self.aligner_dir = Path(aligner_dir) if aligner_dir else None
        self.backend = backend
        self.dtype = dtype
        self.device = device
        self.context = context
        self.max_new_tokens = int(max_new_tokens)
        #: The transcribe stage reads this and cuts the audio accordingly.
        self.chunk_sec = float(chunk_sec) if chunk_sec else None
        self._loaded: Any = None

    # -- references ------------------------------------------------------
    def model_reference(self) -> str:
        """A local directory wins over the hub name, so nothing is downloaded."""
        return str(self.model_dir) if self.model_dir else self.model

    def aligner_reference(self) -> str:
        return str(self.aligner_dir) if self.aligner_dir else self.aligner

    # -- dependency ------------------------------------------------------
    def check(self) -> None:
        _deps.require_qwen_asr()

    def probe(self) -> DependencyStatus:
        if _deps.module_available("qwen_asr"):
            return DependencyStatus(self.meta.name, True, "model %s" % self.model_reference())
        return DependencyStatus(
            self.meta.name, False, "qwen_asr (%s)" % _deps.INSTALL_HINTS["qwen_asr"]
        )

    # -- loading ---------------------------------------------------------
    def _torch_dtype(self) -> Any:
        torch = _deps.require_module("torch", hint="pip install torch")
        return getattr(torch, self.dtype)

    def build_kwargs(self, torch_dtype: Any) -> Dict[str, Any]:
        """Constructor keyword arguments, exposed so a test can assert them."""
        aligner_kwargs = {"dtype": torch_dtype, "device_map": self.device}
        common = {
            "forced_aligner": self.aligner_reference(),
            "forced_aligner_kwargs": aligner_kwargs,
            "max_new_tokens": self.max_new_tokens,
        }
        if self.backend == "vllm":
            return dict(common, model=self.model_reference())
        return dict(common, dtype=torch_dtype, device_map=self.device)

    def load(self) -> Any:
        """Build the model once and keep it, so a chunked run loads it once.

        ``from_pretrained`` and ``LLM`` are two different constructors, not one
        constructor with a flag: that is how the vLLM backend is selected
        upstream.
        """
        if self._loaded is not None:
            return self._loaded
        module = _deps.require_qwen_asr()
        model_class = module.Qwen3ASRModel
        kwargs = self.build_kwargs(self._torch_dtype())
        if self.backend == "vllm":
            self._loaded = model_class.LLM(**kwargs)
        else:
            self._loaded = model_class.from_pretrained(self.model_reference(), **kwargs)
        _out.stage(
            self.meta.name,
            "loaded %s (%s backend)" % (self.model_reference(), self.backend),
        )
        return self._loaded

    # -- inference -------------------------------------------------------
    def transcribe(self, audio: Path, lang: str) -> List[Cue]:
        self.check()
        model = self.load()
        results = model.transcribe(
            audio=str(audio),
            context=self.context,
            language=language_name(lang),
            return_time_stamps=True,
        )
        if not results:
            return []
        items = alignment_items(results[0])
        if not items:
            raise RuntimeError(
                "qwen3_asr returned text but no timestamps: the forced aligner "
                "(%s) is required for cue times. Pass --aligner-dir if the "
                "weights are already on disk." % self.aligner_reference()
            )
        cues = cues_from_alignment(items)
        _out.stage(self.meta.name, "%d items -> %d cues" % (len(items), len(cues)))
        return cues


__all__ = [
    "BACKENDS",
    "CUE_GAP_SEC",
    "DEFAULT_ALIGNER",
    "DEFAULT_CHUNK_SEC",
    "DEFAULT_MODEL",
    "LANGUAGE_NAMES",
    "LARGE_MODEL",
    "MAX_CUE_CHARS",
    "MAX_CUE_SEC",
    "Qwen3AsrEngine",
    "alignment_items",
    "cues_from_alignment",
    "language_name",
]
