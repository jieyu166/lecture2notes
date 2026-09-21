"""Task 5.2 -- adjacent-duplicate suppression in interval mode.

The threshold is inclusive: a mean absolute difference of exactly ``--diff-min``
keeps the frame. That boundary is the whole reason this file exists, because it
is the one decision in the capture path that silently deletes a frame the user
would have wanted.

The spec's table (3.99 dropped / 4.00 kept / 25.3 kept) is stated in mean
absolute difference, which is a real number, while a 64x36 8-bit image can only
produce differences that are a multiple of 1/2304. Both are checked: the table
verbatim against the decision function, and the nearest achievable neighbours
against real PNG files read back through the production signature reader.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL", reason="Pillow synthesises the comparison frames")

from fixtures import synthetic  # noqa: E402

from lecture2notes.frames.interval import (  # noqa: E402
    DEFAULT_DIFF_MIN,
    SIGNATURE_SIZE,
    is_duplicate,
    keep_frame,
    mean_abs_diff,
    select_signatures,
    signature_from_image,
    signature_from_pixels,
)

PIXELS = SIGNATURE_SIZE[0] * SIGNATURE_SIZE[1]  # 64 x 36 = 2304


# --------------------------------------------------------------------------
# the spec's threshold table, verbatim
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "diff, kept, note",
    [
        (3.99, False, "below default threshold"),
        (4.00, True, "threshold is inclusive"),
        (25.3, True, "clear change"),
    ],
)
def test_threshold_table(diff: float, kept: bool, note: str):
    assert is_duplicate(diff, DEFAULT_DIFF_MIN) is (not kept), note


def test_default_threshold_is_four():
    assert DEFAULT_DIFF_MIN == 4.0


def test_keep_frame_applies_the_same_boundary():
    previous = signature_from_pixels([100] * PIXELS)
    # Every pixel off by 4: mean absolute difference is exactly 4.0.
    at_threshold = signature_from_pixels([104] * PIXELS)
    # Every pixel off by 3: exactly 3.0, below it.
    below_threshold = signature_from_pixels([103] * PIXELS)

    assert mean_abs_diff(previous, at_threshold) == 4.0
    assert mean_abs_diff(previous, below_threshold) == 3.0
    assert keep_frame(previous, at_threshold) is True
    assert keep_frame(previous, below_threshold) is False


def test_the_first_frame_is_always_kept():
    assert keep_frame(None, signature_from_pixels([0] * PIXELS)) is True


# --------------------------------------------------------------------------
# the same boundary, through real image files
# --------------------------------------------------------------------------
def test_real_images_at_the_threshold(tmp_path: Path):
    """Exactly 4.00 is kept; the closest value below it is dropped.

    ``hot`` pixels at 255 over a black field give a mean absolute difference of
    ``hot * 255 / 2304``: 36 hot pixels is 3.984, one step under the threshold,
    and 37 is 4.095, one step over.
    """
    base = signature_from_image(synthetic.gray_image(tmp_path / "base.png", 0))
    uniform = signature_from_image(synthetic.gray_image(tmp_path / "plus4.png", 4))
    just_below = signature_from_image(
        synthetic.speckled_image(tmp_path / "below.png", 0, hot_pixels=36)
    )
    just_above = signature_from_image(
        synthetic.speckled_image(tmp_path / "above.png", 0, hot_pixels=37)
    )

    assert mean_abs_diff(base, uniform) == pytest.approx(4.0)
    assert mean_abs_diff(base, just_below) == pytest.approx(36 * 255 / PIXELS)
    assert mean_abs_diff(base, just_below) < DEFAULT_DIFF_MIN
    assert mean_abs_diff(base, just_above) > DEFAULT_DIFF_MIN

    assert keep_frame(base, uniform) is True
    assert keep_frame(base, just_below) is False
    assert keep_frame(base, just_above) is True


def test_a_clear_change_is_always_kept(tmp_path: Path):
    dark = signature_from_image(synthetic.gray_image(tmp_path / "dark.png", 10))
    bright = signature_from_image(synthetic.gray_image(tmp_path / "bright.png", 200))

    assert mean_abs_diff(dark, bright) == pytest.approx(190.0)
    assert keep_frame(dark, bright) is True


def test_ten_identical_frames_keep_only_the_first(tmp_path: Path):
    """A speaker dwelling on one slide must not produce ten copies of it."""
    signatures = [
        signature_from_image(synthetic.gray_image(tmp_path / ("same-%02d.png" % n), 120))
        for n in range(10)
    ]

    kept = select_signatures(signatures)

    assert kept == [0]
    assert len(signatures) - len(kept) == 9


def test_comparison_is_against_the_last_kept_frame():
    """A slow drift must yield one frame per visible change, not one per sample."""
    # Steps of 2 are each below the threshold, but the total drift is 8.
    signatures = [signature_from_pixels([100 + 2 * step] * PIXELS) for step in range(5)]

    kept = select_signatures(signatures)

    # 100 -> 102 -> 104: the third sample is 4 away from the last kept one.
    assert kept == [0, 2, 4]


def test_a_custom_threshold_is_honoured():
    previous = signature_from_pixels([0] * PIXELS)
    current = signature_from_pixels([10] * PIXELS)

    assert keep_frame(previous, current, diff_min=25.0) is False
    assert keep_frame(previous, current, diff_min=10.0) is True


def test_signatures_of_different_lengths_are_refused():
    with pytest.raises(ValueError, match="same length"):
        mean_abs_diff([0, 0, 0], [0, 0])


# --------------------------------------------------------------------------
# the capture loop actually applies it
# --------------------------------------------------------------------------
def test_capture_drops_duplicates_and_reports_the_count(tmp_path: Path, monkeypatch):
    """The dedup runs inside capture, and the dropped file is removed from disk."""
    from lecture2notes.frames import capture

    video = tmp_path / "talk.mp4"
    video.write_bytes(b"not a real video; extraction is injected")
    monkeypatch.setattr(capture._deps, "require_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(capture._deps, "require_ffprobe", lambda: "ffprobe")
    monkeypatch.setattr(capture.scene_mode, "get_duration", lambda path: 50.0)

    # Samples at 0, 10, 20, 30, 40: the middle three are the same picture.
    shades = {0: 10, 10: 120, 20: 120, 30: 120, 40: 220}

    def fake_extract(source, second, out_path, width):
        synthetic.gray_image(Path(out_path), shades[int(second)], size=(64, 36))
        return True

    result = capture.capture(
        video, mode="interval", every=10, extract=fake_extract, merge=False
    )

    assert result.evaluated == 5
    assert result.kept == 3
    assert result.dropped == 2
    kept_names = sorted(path.name for path in result.frames_dir.iterdir())
    assert kept_names == ["talk-0000.png", "talk-0010.png", "talk-0040.png"]
