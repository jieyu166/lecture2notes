"""OCR every captured frame once, cache the result, attach it to the segments.

Ported from rad-workflow skills/lecture-to-notes/scripts/ocr_frames.py.
Inspired by drpwchen/lecture-to-notes scripts/quick_ocr.py @79053a3

OCR text is a positioning clue, not ground truth. The source precedence does not
change because OCR exists: official handout first, then the slide as a human
reads it, then the transcript, then OCR. The engine will read a classification
label as one word, get a number wrong, and flatten a table into a scrambled
line. Use it to decide which slide a segment is about; go back to the image for
the terminology and the numbers.

The cache is keyed on size plus mtime, so a re-run only pays for frames that
actually changed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from urllib.parse import unquote

from lecture2notes import _deps, _out
from lecture2notes.schema.io import read_json, write_json_atomic

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
CACHE_SUFFIX = ".frames_ocr.json"
DOCUMENT_SUFFIX = ".json"
ENGINE_NAME = "rapidocr-onnxruntime"
DEFAULT_MIN_CONF = 0.5

SOURCE_PRECEDENCE_NOTE = (
    "OCR text carries recognition error and is for locating and cross-checking only; "
    "terminology, numbers and classification criteria come from the official handout, "
    "and from the original image when in doubt. "
    "Source precedence: official handout > slide screenshot > ASR transcript > OCR."
)

# A meeting recording always has a toolbar burned into the picture, and OCR reads
# it as slide content. On one measured batch, 543 of 6880 OCR lines (7.9%) were
# this. The engine misspells the overlay differently every time, so the filter is
# a set of fuzzy patterns rather than a list of strings. Left in, these phrases
# match every single slide and make cross-lecture search useless.
UI_NOISE = [
    re.compile(r"正在[觀翻檢][看著].{0,4}[幕募]"),
    re.compile(r"檢[視祝現].{0,2}[選避逛]項"),
    re.compile(r"^\W{0,2}(REC|EC)\W{0,2}$", re.I),
    re.compile(r"分享音訊.{0,6}靜音"),
    re.compile(r"^(靜音|取消靜音|停止視訊|參加者|聊天|分享畫面|結束會議)$"),
    re.compile(r"^(mute|unmute|stop video|participants|chat|share screen|leave meeting)$", re.I),
]


def strip_ui(text: str) -> str:
    """Drop whole lines that match a meeting-toolbar pattern."""
    kept = [
        line for line in text.split("\n")
        if line.strip() and not any(pattern.search(line.strip()) for pattern in UI_NOISE)
    ]
    return "\n".join(kept)


def fingerprint(path: Path) -> str:
    """Cheap change detector: size plus whole-second mtime."""
    stat = Path(path).stat()
    return "%d:%d" % (stat.st_size, int(stat.st_mtime))


def load_engine():
    """RapidOCR, or a missing-dependency error. Never a silent skip.

    Degrading quietly here produces a JSON that looks complete and is missing a
    whole layer.
    """
    module = _deps.require_rapidocr()
    return module.RapidOCR()


def load_s2t(enabled: bool = True):
    """Optional Simplified-to-Traditional pass over the OCR output.

    The model often reads a Traditional glyph as its Simplified counterpart.
    Converting makes the text easier to match against a handout; it does not make
    the recognition more accurate, and a misread is still a misread.
    """
    if not enabled:
        return None
    try:
        from opencc import OpenCC  # type: ignore
    except ImportError:
        _out.say("warn", "opencc not installed; OCR text is left as recognised")
        _out.say("info", "install: %s" % _deps.INSTALL_HINTS["opencc"])
        return None
    return OpenCC("s2twp").convert


def lines_from_result(result: Any, min_conf: float = DEFAULT_MIN_CONF) -> str:
    """Turn a RapidOCR result into text, dropping low-confidence lines.

    Pure, so the filtering rule can be tested without the engine.
    """
    if not result:
        return ""
    lines: List[str] = []
    for item in result:
        if len(item) < 3:
            continue
        text = str(item[1]).strip()
        try:
            score = float(item[2])
        except (TypeError, ValueError):
            continue
        if text and score >= min_conf:
            lines.append(text)
    return "\n".join(lines)


def ocr_one(engine: Any, path: Path, min_conf: float = DEFAULT_MIN_CONF) -> str:
    result, _elapsed = engine(str(path))
    return lines_from_result(result, min_conf)


def collect_frames(data: Optional[Mapping[str, Any]], folder: Optional[Path] = None) -> List[str]:
    """Every frame referenced by the document, de-duplicated, in order.

    With no document, list the image files in ``folder`` instead.
    """
    seen: set = set()
    out: List[str] = []
    if data is not None:
        for segment in data.get("segments", []):
            if not isinstance(segment, Mapping):
                continue
            frames = segment.get("frames") or (
                [segment["frame"]] if segment.get("frame") else []
            )
            for frame in frames:
                key = str(frame)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
        return out
    if folder is None:
        return out
    folder = Path(folder)
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() in IMG_EXT:
            out.append("%s/%s" % (folder.name, path.name))
    return out


def pending_frames(
    frames: Sequence[str],
    cache: Mapping[str, Any],
    base_dir: Path,
    force: bool = False,
) -> List[str]:
    """Which frames still need OCR, given the cache."""
    todo: List[str] = []
    for frame in frames:
        path = Path(base_dir) / unquote(frame)
        if not path.exists():
            continue
        hit = cache.get(frame)
        if hit and not force and hit.get("fingerprint") == fingerprint(path):
            continue
        todo.append(frame)
    return todo


def cache_report(total: int, todo: int) -> str:
    """The exact line the OCR stage prints, so a test can assert on it."""
    return "%d frames, %d need OCR (%d cached)" % (total, todo, total - todo)


def read_cache(path: Path) -> Dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _out.say("warn", "OCR cache is unreadable, rebuilding: %s" % path.name)
        return {}


def write_cache(path: Path, cache: Mapping[str, Any]) -> Path:
    return write_json_atomic(path, dict(cache))


def merge_ocr_into_segments(
    data: Mapping[str, Any],
    cache: Mapping[str, Any],
    min_conf: float = DEFAULT_MIN_CONF,
    s2t: bool = True,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Attach ``frame_ocr`` to each segment and stamp ``ocr_meta`` on the document."""
    result = dict(data)
    merged = 0
    segments = result.get("segments")
    frames_seen: List[str] = []
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            segment_frames = segment.get("frames") or (
                [segment["frame"]] if segment.get("frame") else []
            )
            entries = []
            for frame in segment_frames:
                frames_seen.append(str(frame))
                hit = cache.get(str(frame))
                if hit and hit.get("text"):
                    entries.append({"frame": str(frame), "text": hit["text"]})
            if entries:
                segment["frame_ocr"] = entries
                merged += 1
            else:
                segment.pop("frame_ocr", None)
    result["ocr_meta"] = {
        "engine": ENGINE_NAME,
        "generated_at": generated_at
        or datetime.now().astimezone().isoformat(timespec="seconds"),
        "min_conf": min_conf,
        "s2t": bool(s2t),
        "frames_total": len(frames_seen),
        "frames_with_text": sum(
            1 for frame in frames_seen if (cache.get(frame) or {}).get("text")
        ),
        "segments_with_text": merged,
        "note": SOURCE_PRECEDENCE_NOTE,
    }
    return result


# ---------------------------------------------------------------------------
# the stage itself
# ---------------------------------------------------------------------------
@dataclass
class OcrResult:
    """What one ``l2n ocr`` run did, in numbers a caller can assert on."""

    target: Path
    cache_path: Path
    frames: List[str] = field(default_factory=list)
    recognised: List[str] = field(default_factory=list)
    cached: int = 0
    engine_calls: int = 0
    document: Optional[Path] = None

    @property
    def total(self) -> int:
        return len(self.frames)

    @property
    def pending(self) -> int:
        return self.total - self.cached


def cache_path_for(target: Path) -> Path:
    """``<stem>.frames_ocr.json`` beside the document, or beside the folder."""
    target = Path(target)
    if target.is_dir():
        return target.parent / (target.name + CACHE_SUFFIX)
    name = target.name
    if name.endswith(DOCUMENT_SUFFIX):
        name = name[: -len(DOCUMENT_SUFFIX)]
    return target.parent / (name + CACHE_SUFFIX)


def run_ocr(
    target: Path,
    min_conf: float = DEFAULT_MIN_CONF,
    s2t: bool = True,
    force: bool = False,
    engine_factory: Optional[Callable[[], Any]] = None,
    progress_interval: float = 5.0,
) -> OcrResult:
    """OCR every frame the target references, using the cache where it can.

    The engine is built lazily and only when at least one frame actually needs
    recognising. RapidOCR loads an ONNX runtime and several hundred megabytes of
    model; paying that to discover there is nothing to do is the difference
    between a cached re-run taking a second and taking half a minute.
    """
    target = Path(target)
    document: Optional[Path] = None
    data: Optional[Dict[str, Any]] = None
    if target.is_dir():
        base_dir = target.parent
        frames = collect_frames(None, target)
    else:
        document = target
        data = read_json(target)
        base_dir = target.parent
        frames = collect_frames(data, None)

    cache_file = cache_path_for(target)
    cache = read_cache(cache_file)
    todo = pending_frames(frames, cache, base_dir, force)
    result = OcrResult(
        target=target,
        cache_path=cache_file,
        frames=list(frames),
        cached=len(frames) - len(todo),
        document=document,
    )
    _out.stage("ocr", cache_report(len(frames), len(todo)))

    if todo:
        engine = (engine_factory or load_engine)()
        convert = load_s2t(s2t)
        progress = _out.Progress("ocr", len(todo), interval=progress_interval)
        for frame in todo:
            path = Path(base_dir) / unquote(frame)
            text = strip_ui(ocr_one(engine, path, min_conf))
            result.engine_calls += 1
            if convert is not None and text:
                text = convert(text)
            cache[frame] = {
                "fingerprint": fingerprint(path),
                "chars": len(text),
                "text": text,
            }
            if text:
                result.recognised.append(frame)
            progress.advance()
        progress.finish()
        write_cache(cache_file, cache)
    else:
        result.recognised = [
            frame for frame in frames if (cache.get(frame) or {}).get("text")
        ]

    if data is not None and document is not None:
        write_json_atomic(document, merge_ocr_into_segments(data, cache, min_conf, s2t))
    return result


__all__ = [
    "CACHE_SUFFIX",
    "DEFAULT_MIN_CONF",
    "ENGINE_NAME",
    "IMG_EXT",
    "SOURCE_PRECEDENCE_NOTE",
    "UI_NOISE",
    "cache_report",
    "collect_frames",
    "fingerprint",
    "lines_from_result",
    "load_engine",
    "load_s2t",
    "OcrResult",
    "cache_path_for",
    "merge_ocr_into_segments",
    "ocr_one",
    "pending_frames",
    "read_cache",
    "run_ocr",
    "strip_ui",
    "write_cache",
]
