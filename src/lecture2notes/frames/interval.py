"""Fixed-interval capture with adjacent-duplicate suppression.

Consolidated from a procedure script written for this project, which existed
because scene detection is the wrong tool for a recording of somebody scrolling
through a continuously changing image: nothing in it is a "slide change", and an
adaptive detector finds a handful of cuts across an entire session, leaving the
lecture with no usable visual layer at all.

So: sample every N seconds, then drop a sample that looks essentially identical
to the last one kept. A 64x36 grayscale thumbnail is compared by mean absolute
difference; below the threshold the sample is discarded, which is what stops a
speaker dwelling on one image from producing fifty copies of it.

The threshold comparison is ``>= diff_min``, so a frame exactly at the threshold
is kept. Boundaries are written down here rather than left to whoever reads the
code next.

The output record shape is identical to scene mode's, so nothing downstream has
to know which mode ran.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from lecture2notes import _deps

#: Sampling interval in seconds.
DEFAULT_INTERVAL = 45
#: Mean absolute difference (0-255) below which a sample is a duplicate.
DEFAULT_DIFF_MIN = 4.0
#: Thumbnail size used for the comparison.
SIGNATURE_SIZE = (64, 36)
#: Width the kept frames are scaled to.
DEFAULT_WIDTH = 1280


def plan_marks(duration_sec: float, every: int = DEFAULT_INTERVAL) -> List[int]:
    """The sample times for a recording of this length."""
    if every <= 0:
        raise ValueError("interval must be positive")
    return list(range(0, int(duration_sec), int(every)))


def mean_abs_diff(left: Sequence[int], right: Sequence[int]) -> float:
    """Mean absolute difference between two equally sized grayscale signatures."""
    if len(left) != len(right):
        raise ValueError("signatures must be the same length")
    if not left:
        return 0.0
    return sum(abs(int(a) - int(b)) for a, b in zip(left, right)) / float(len(left))


def is_duplicate(diff: float, diff_min: float = DEFAULT_DIFF_MIN) -> bool:
    """Strictly below the threshold is a duplicate; exactly at it is not."""
    return diff < diff_min


def keep_frame(
    previous: Optional[Sequence[int]],
    current: Sequence[int],
    diff_min: float = DEFAULT_DIFF_MIN,
) -> bool:
    """Should this sample be kept? The first sample always is."""
    if previous is None:
        return True
    return not is_duplicate(mean_abs_diff(previous, current), diff_min)


def select_signatures(
    signatures: Iterable[Sequence[int]],
    diff_min: float = DEFAULT_DIFF_MIN,
) -> List[int]:
    """Indices of the signatures that survive deduplication, in order.

    Comparison is always against the last *kept* signature, not the last one
    seen, so a slow drift across many samples still yields one frame per visible
    change rather than one per sample.
    """
    kept: List[int] = []
    previous: Optional[Sequence[int]] = None
    for index, signature in enumerate(signatures):
        if keep_frame(previous, signature, diff_min):
            kept.append(index)
            previous = signature
    return kept


def signature_from_pixels(pixels: Sequence[int], size: Tuple[int, int] = SIGNATURE_SIZE) -> Tuple[int, ...]:
    """Validate and freeze a raw grayscale buffer as a signature."""
    width, height = size
    if len(pixels) != width * height:
        raise ValueError("expected %d grayscale samples, got %d" % (width * height, len(pixels)))
    return tuple(int(value) for value in pixels)


def signature_from_image(path: Path, size: Tuple[int, int] = SIGNATURE_SIZE) -> Tuple[int, ...]:
    """Downscale an image file to a grayscale signature.

    Pillow is used rather than OpenCV: the capture path only needs a resize and a
    grayscale conversion, and OpenCV would add a large dependency for that alone.
    """
    module = _deps.require_module("pillow", import_name="PIL.Image")
    with module.open(str(path)) as image:
        thumbnail = image.convert("L").resize(size)
        # ``tobytes`` on a mode-L image is one byte per pixel in row order, which
        # is exactly the signature and, unlike ``getdata``, is not deprecated.
        return tuple(thumbnail.tobytes())


__all__ = [
    "DEFAULT_DIFF_MIN",
    "DEFAULT_INTERVAL",
    "DEFAULT_WIDTH",
    "SIGNATURE_SIZE",
    "is_duplicate",
    "keep_frame",
    "mean_abs_diff",
    "plan_marks",
    "select_signatures",
    "signature_from_image",
    "signature_from_pixels",
]
