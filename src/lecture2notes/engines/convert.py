"""`l2n convert-model`: build the CTranslate2 weights the default engine needs.

New module for this package. Breeze-ASR-25 is published as a Hugging Face
checkpoint; faster-whisper runs CTranslate2. Converting is a one-time step, but
it is a one-time step that downloads several gigabytes and then writes several
more, so this module is built around two refusals:

* **Say the cost first.** The disk estimate is printed before anything is
  downloaded or written, because finding out half way through that the drive is
  full leaves a half-written model directory that looks valid to a later run.
* **Never overwrite silently.** An existing target directory is refused unless
  ``--force``. A converted model is expensive to rebuild and the user may have
  pointed several projects at it.

The overwrite check runs *before* the dependency check on purpose: someone whose
model already exists should be told exactly that, not handed an install
instruction for a package they do not need.

The converter is injected, so the tests assert the arguments without a 6 GB
download and without ctranslate2 installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from lecture2notes import _deps, _out
from lecture2notes.engines.breeze_ct2 import SOURCE_MODEL, default_model_dir

#: float16 halves the model with no measurable transcription difference on this
#: model; float32 exists for a machine whose GPU cannot do half precision.
DEFAULT_QUANTIZATION = "float16"
QUANTIZATIONS = ("float16", "float32", "int8", "int8_float16")

#: Approximate gigabytes the Hugging Face checkpoint occupies once downloaded.
SOURCE_GB = 6.0
#: Approximate gigabytes the converted directory occupies, per quantization.
OUTPUT_GB: Dict[str, float] = {
    "float16": 3.0,
    "float32": 6.0,
    "int8": 1.6,
    "int8_float16": 1.6,
}

#: Tokeniser and preprocessor files faster-whisper expects next to model.bin.
COPY_FILES = ("tokenizer.json", "preprocessor_config.json", "vocabulary.json")


class ConversionRefused(Exception):
    """The conversion will not start: the reason is already in the message."""


def output_estimate_gb(quantization: str) -> float:
    return OUTPUT_GB.get(quantization, OUTPUT_GB[DEFAULT_QUANTIZATION])


def disk_report(quantization: str = DEFAULT_QUANTIZATION) -> List[str]:
    """The lines printed before any download starts. ASCII only."""
    output = output_estimate_gb(quantization)
    return [
        "source checkpoint download: about %.1f GB" % SOURCE_GB,
        "converted %s output: about %.1f GB" % (quantization, output),
        "peak disk needed: about %.1f GB" % (SOURCE_GB + output),
    ]


def _default_converter(source: str, copy_files: List[str]) -> Any:
    """The real ctranslate2 converter. Only reached outside the tests."""
    _deps.require_module("transformers")
    ctranslate2 = _deps.require_module("ctranslate2")
    from ctranslate2.converters import TransformersConverter  # type: ignore

    assert ctranslate2 is not None
    return TransformersConverter(source, copy_files=copy_files)


def check_target(out_dir: Path, force: bool = False) -> Path:
    """Refuse an existing, non-empty target directory unless ``force``."""
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()) and not force:
        raise ConversionRefused(
            "target already exists and is not empty: %s. Pass --force to "
            "overwrite it, or --out <dir> to convert somewhere else." % out_dir
        )
    return out_dir


def convert(
    out_dir: Optional[Path] = None,
    source: str = SOURCE_MODEL,
    quantization: str = DEFAULT_QUANTIZATION,
    force: bool = False,
    converter_factory: Optional[Callable[..., Any]] = None,
    copy_files: Optional[List[str]] = None,
) -> Path:
    """Convert ``source`` into a CTranslate2 directory and return its path.

    Raises :class:`ConversionRefused` (the CLI turns that into exit 2) and
    ``_deps.MissingDependency`` (exit 3). Nothing is written before both checks
    have passed.
    """
    if quantization not in QUANTIZATIONS:
        raise ConversionRefused(
            "unknown quantization: %s (known: %s)" % (quantization, ", ".join(QUANTIZATIONS))
        )
    target = Path(out_dir) if out_dir else default_model_dir()

    for text in disk_report(quantization):
        _out.say("info", text)
    _out.stage("convert-model", "%s -> %s" % (source, target))

    check_target(target, force)

    factory = converter_factory or _default_converter
    converter = factory(source, list(copy_files or COPY_FILES))

    target.parent.mkdir(parents=True, exist_ok=True)
    converter.convert(output_dir=str(target), quantization=quantization, force=force)
    _out.ok("converted to %s" % target)
    return target


__all__ = [
    "COPY_FILES",
    "SOURCE_MODEL",
    "DEFAULT_QUANTIZATION",
    "OUTPUT_GB",
    "QUANTIZATIONS",
    "SOURCE_GB",
    "ConversionRefused",
    "check_target",
    "convert",
    "disk_report",
    "output_estimate_gb",
]
