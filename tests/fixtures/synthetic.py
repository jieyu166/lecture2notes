"""Synthetic frames and a synthetic lecture video for the capture tests.

Nothing here is committed as a file: the images and the 30-second video are
built into ``tmp_path`` when a test asks for them and vanish with it. Two rules
made this the only acceptable fixture strategy for this package:

* no real lecture recording may enter the repository, and
* a capture test that needs a 100 MB download is a test nobody runs.

The slides are flat colours with a large numeral painted on them. Flat colour
gives scene detection an unambiguous cut and gives the interval dedup an
unambiguous grayscale difference; the numeral stops libx264 from collapsing a
uniform field into something that decodes back a shade off.

``build_video`` needs ffmpeg. Callers are expected to skip the whole module when
it is absent rather than fail: see the guard at the top of ``test_frames.py``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

#: Even dimensions, because yuv420p refuses odd ones.
SIZE = (640, 360)

#: Six visually distinct slides: 6 x 5 seconds is the 30-second fixture.
SLIDE_COLORS: Tuple[Tuple[int, int, int], ...] = (
    (16, 16, 16),
    (200, 40, 40),
    (40, 190, 60),
    (40, 60, 200),
    (225, 215, 40),
    (190, 40, 190),
)

DEFAULT_SECONDS_EACH = 5.0
DEFAULT_FPS = 10


def _image_module():
    from PIL import Image  # noqa: PLC0415 - optional test-only dependency

    return Image


def solid_image(
    path: Path,
    color: Tuple[int, int, int],
    label: str = "",
    size: Tuple[int, int] = SIZE,
) -> Path:
    """Write one flat-colour PNG, optionally with a big numeral on it."""
    from PIL import ImageDraw  # noqa: PLC0415 - optional test-only dependency

    image = _image_module().new("RGB", size, color)
    if label:
        draw = ImageDraw.Draw(image)
        width, height = size
        # No font file is loaded on purpose: the default bitmap font is always
        # present, and a rectangle plus the default glyphs is enough contrast.
        draw.rectangle(
            [width // 4, height // 4, width * 3 // 4, height * 3 // 4],
            outline=(255, 255, 255),
            width=6,
        )
        draw.text((width // 2 - 8, height // 2 - 4), label, fill=(255, 255, 255))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def gray_image(path: Path, value: int, size: Tuple[int, int] = (64, 36)) -> Path:
    """A uniform grayscale PNG, for exact mean-absolute-difference arithmetic."""
    image = _image_module().new("L", size, int(value))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def speckled_image(
    path: Path,
    background: int,
    hot_pixels: int,
    hot_value: int = 255,
    size: Tuple[int, int] = (64, 36),
) -> Path:
    """A grayscale PNG with exactly ``hot_pixels`` pixels set to ``hot_value``.

    The mean absolute difference against ``gray_image(background)`` is then
    ``hot_pixels * abs(hot_value - background) / (w * h)`` exactly, which is how
    the threshold table is pinned to real image files rather than to floats.
    """
    width, height = size
    if not 0 <= hot_pixels <= width * height:
        raise ValueError("hot_pixels out of range")
    image = _image_module().new("L", size, int(background))
    pixels = image.load()
    for offset in range(hot_pixels):
        pixels[offset % width, offset // width] = int(hot_value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def build_slides(
    folder: Path,
    colors: Optional[Sequence[Tuple[int, int, int]]] = None,
    prefix: str = "slide",
) -> List[Path]:
    """Write one PNG per colour and return them in order."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    chosen = list(colors if colors is not None else SLIDE_COLORS)
    return [
        solid_image(folder / ("%s-%02d.png" % (prefix, index)), color, str(index))
        for index, color in enumerate(chosen)
    ]


def build_video(
    folder: Path,
    slides: Sequence[Path],
    name: str = "lecture.mp4",
    seconds_each: float = DEFAULT_SECONDS_EACH,
    fps: int = DEFAULT_FPS,
) -> Path:
    """Concatenate still images into an mp4 of ``len(slides) * seconds_each``.

    The last entry is repeated without a duration line: the concat demuxer
    otherwise gives the final image a single frame, and the video comes out one
    slide short. The repeat overshoots instead (it inherits the preceding
    duration), so ``-t`` trims the result back to exactly the requested length.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not on PATH")
    folder = Path(folder)
    listing = folder / "concat.txt"
    lines: List[str] = []
    for slide in slides:
        lines.append("file '%s'" % Path(slide).name)
        lines.append("duration %g" % seconds_each)
    lines.append("file '%s'" % Path(slides[-1]).name)
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = folder / name
    result = subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-r", str(fps), "-pix_fmt", "yuv420p", "-c:v", "libx264",
            "-preset", "ultrafast",
            "-t", "%g" % (len(slides) * seconds_each), str(out),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    listing.unlink(missing_ok=True)
    if result.returncode != 0 or not out.is_file():
        raise RuntimeError("ffmpeg failed to build the fixture video: %s" % result.stderr)
    return out


def build_lecture_video(folder: Path, name: str = "lecture.mp4") -> Path:
    """The standard 30-second, six-slide fixture."""
    slides_dir = Path(folder) / "_slides"
    slides = build_slides(slides_dir)
    return build_video(slides_dir, slides, name=name).rename(Path(folder) / name)


__all__ = [
    "DEFAULT_FPS",
    "DEFAULT_SECONDS_EACH",
    "SIZE",
    "SLIDE_COLORS",
    "build_lecture_video",
    "build_slides",
    "build_video",
    "gray_image",
    "solid_image",
    "speckled_image",
]
