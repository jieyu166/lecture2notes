"""One error path for every missing external dependency.

Stages never degrade silently. When an external tool, Python package or model
directory is missing, the stage raises :class:`MissingDependency` before touching
the filesystem; the CLI layer prints two lines (what is missing, how to install
it) and exits 3.
"""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path
from typing import Callable, Dict, Optional

from lecture2notes import _out


class MissingDependency(Exception):
    """Raised when a required external dependency is absent.

    ``name`` is the short identifier printed to the user; ``how`` is the exact
    command or action that installs it.
    """

    def __init__(self, name: str, how: str, detail: str = "") -> None:
        self.name = name
        self.how = how
        self.detail = detail
        super().__init__("missing dependency: %s" % name)

    def report(self) -> None:
        """Print the standard two-line report."""
        _out.error("missing dependency: %s" % self.name)
        _out.say("info", "install: %s" % self.how)
        if self.detail:
            _out.say("info", self.detail)


# Install hints are deliberately literal commands, not prose.
INSTALL_HINTS: Dict[str, str] = {
    "ffmpeg": "winget install Gyan.FFmpeg  (or: https://ffmpeg.org/download.html)",
    "ffprobe": "winget install Gyan.FFmpeg  (or: https://ffmpeg.org/download.html)",
    "rapidocr": "pip install rapidocr-onnxruntime",
    "qwen_asr": "pip install lecture2notes[qwen]",
    "faster_whisper": "pip install lecture2notes[breeze]",
    "ctranslate2": "pip install lecture2notes[breeze]",
    "scenedetect": "pip install lecture2notes[scene]",
    "pillow": "pip install Pillow",
    "opencc": "pip install opencc-python-reimplemented",
    "whisper_cpp": "build whisper.cpp and pass --whisper-cpp-bin <path to main.exe>",
    "ct2_model": "l2n convert-model --out <dir>",
}


def _hint(name: str) -> str:
    return INSTALL_HINTS.get(name, "see README.md for installation instructions")


def require_binary(name: str, hint: Optional[str] = None) -> str:
    """Return the absolute path of an executable on PATH, or raise."""
    found = shutil.which(name)
    if not found:
        raise MissingDependency(name, hint or _hint(name))
    return found


def require_module(name: str, import_name: Optional[str] = None,
                   hint: Optional[str] = None):
    """Import and return a Python module, or raise."""
    module_name = import_name or name
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise MissingDependency(
            name, hint or _hint(name), "import %s failed: %s" % (module_name, exc)
        ) from exc


def require_ffmpeg() -> str:
    return require_binary("ffmpeg")


def require_ffprobe() -> str:
    return require_binary("ffprobe")


def require_rapidocr():
    """RapidOCR ships under two distribution names; accept either import path."""
    for module_name in ("rapidocr_onnxruntime", "rapidocr"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue
    raise MissingDependency("rapidocr", _hint("rapidocr"))


def require_qwen_asr():
    return require_module("qwen_asr", import_name="qwen_asr")


def require_ct2_model(model_dir) -> Path:
    """Check that a CTranslate2 model directory exists and looks like one."""
    path = Path(model_dir)
    if not path.is_dir():
        raise MissingDependency(
            "ct2_model", _hint("ct2_model"), "not a directory: %s" % path
        )
    if not (path / "model.bin").is_file():
        raise MissingDependency(
            "ct2_model", _hint("ct2_model"), "model.bin not found in: %s" % path
        )
    return path


_CHECKS: Dict[str, Callable[..., object]] = {
    "ffmpeg": require_ffmpeg,
    "ffprobe": require_ffprobe,
    "rapidocr": require_rapidocr,
    "qwen_asr": require_qwen_asr,
    "ct2_model": require_ct2_model,
}


def require(name: str, *args, **kwargs):
    """Dispatch to the named requirement check.

    ``require("ffmpeg")``, ``require("rapidocr")``, ``require("qwen_asr")`` or
    ``require("ct2_model", some_dir)``.
    """
    check = _CHECKS.get(name)
    if check is None:
        raise KeyError("unknown dependency check: %s" % name)
    return check(*args, **kwargs)


__all__ = [
    "INSTALL_HINTS",
    "MissingDependency",
    "require",
    "require_binary",
    "require_ct2_model",
    "require_ffmpeg",
    "require_ffprobe",
    "require_module",
    "require_qwen_asr",
    "require_rapidocr",
]
