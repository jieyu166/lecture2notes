"""Smoke tests for the frame curator ported from frame_curator.py."""

from __future__ import annotations

import hashlib

import pytest

from lecture2notes.frames.curator import (
    FrameCurationError,
    candidate_score,
    curate,
    hamming_distance,
    is_duplicate,
    merge_candidates,
    perceptual_hash,
    quality_from_pixels,
    resolve_within,
    safe_relative_path,
    select_for_segment,
    validate_candidate,
)


def _quality(sharpness=40.0, luma=128.0, phash="ff00"):
    return {"readable": True, "luma": luma, "sharpness": sharpness, "phash": phash}


def _stage(tmp_path, name, payload):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_safe_relative_path_rejects_escapes_and_absolutes():
    assert safe_relative_path("frames/a.png").as_posix() == "frames/a.png"
    assert safe_relative_path("frames\\a.png").as_posix() == "frames/a.png"
    assert safe_relative_path("../a.png") is None
    assert safe_relative_path("/etc/passwd") is None
    assert safe_relative_path("C:/Windows/a.png") is None
    assert safe_relative_path("a\nb.png") is None
    assert safe_relative_path(None) is None


def test_resolve_within_refuses_to_leave_the_root(tmp_path):
    assert resolve_within(tmp_path, safe_relative_path("frames/a.png")) is not None


def test_perceptual_hash_and_hamming_distance():
    dark = perceptual_hash(bytes([0, 0, 255, 255]))
    same = perceptual_hash(bytes([0, 0, 255, 255]))
    other = perceptual_hash(bytes([255, 255, 0, 0]))

    assert dark == same
    assert hamming_distance(dark, same) == 0
    assert hamming_distance(dark, other) > 0
    assert perceptual_hash(b"") == ""


def test_quality_from_pixels_reports_unreadable_on_a_short_buffer():
    assert quality_from_pixels([0, 1, 2], size=32)["readable"] is False

    flat = quality_from_pixels([120] * (4 * 4), size=4)
    assert flat["readable"] is True
    assert flat["luma"] == 120.0
    assert flat["sharpness"] == 0.0


def test_candidate_score_prefers_sharp_mid_luma_frames_with_text():
    sharp = {"quality": _quality(sharpness=50.0), "ocr": "x" * 200}
    blurry = {"quality": _quality(sharpness=2.0), "ocr": ""}

    assert candidate_score(sharp) > candidate_score(blurry)


def test_is_duplicate_uses_the_hamming_threshold():
    first = {"quality": _quality(phash="ffff")}
    same = {"quality": _quality(phash="ffff")}
    different = {"quality": _quality(phash="0000")}

    assert is_duplicate(same, [first]) is True
    assert is_duplicate(different, [first]) is False


def test_merge_candidates_drops_only_identical_path_and_time():
    merged = merge_candidates(
        [{"path": "a.png", "time": 1.0}, {"path": "a.png", "time": 1.0}],
        [{"path": "a.png", "time": 2.0}, {"path": "../bad.png", "time": 3.0}],
    )

    assert [(c["path"], c["time"]) for c in merged] == [("a.png", 1.0), ("a.png", 2.0)]


def test_validate_candidate_rejects_a_tampered_file(tmp_path):
    digest = _stage(tmp_path, "candidates/a.png", b"original")
    candidate = {
        "identity": "c1", "path": "candidates/a.png", "time": 5.0,
        "asset_sha256": digest, "quality": _quality(),
    }
    assert validate_candidate(candidate, tmp_path, 0.0, 10.0)[1] is None

    (tmp_path / "candidates" / "a.png").write_bytes(b"tampered")
    assert validate_candidate(candidate, tmp_path, 0.0, 10.0)[1] == "candidate_hash"


def test_validate_candidate_rejects_out_of_range_and_blank_frames(tmp_path):
    digest = _stage(tmp_path, "candidates/b.png", b"payload")
    base = {
        "identity": "c2", "path": "candidates/b.png",
        "asset_sha256": digest, "quality": _quality(),
    }

    assert validate_candidate({**base, "time": 99.0}, tmp_path, 0.0, 10.0)[1] == "candidate_time"
    blank = {**base, "time": 5.0, "quality": _quality(luma=0.5)}
    assert validate_candidate(blank, tmp_path, 0.0, 10.0)[1] == "candidate_blank"
    blurred = {**base, "time": 5.0, "quality": _quality(sharpness=0.1)}
    assert validate_candidate(blurred, tmp_path, 0.0, 10.0)[1] == "candidate_blurred"


def test_select_for_segment_caps_and_orders_by_time(tmp_path):
    candidates = []
    for index in range(5):
        name = "candidates/f%d.png" % index
        digest = _stage(tmp_path, name, b"payload-%d" % index)
        candidates.append({
            "identity": "c%d" % index, "path": name, "time": float(10 - index),
            "asset_sha256": digest,
            "quality": _quality(sharpness=10.0 + index, phash="%s" % (str(index) * 4)),
        })

    selected, findings = select_for_segment(candidates, tmp_path, 0.0, 20.0, max_per_segment=3)

    assert len(selected) == 3
    assert [item[0]["time"] for item in selected] == sorted(item[0]["time"] for item in selected)
    assert all(finding["severity"] == "warning" for finding in findings)


def test_curate_promotes_frames_and_writes_a_manifest(tmp_path):
    digest = _stage(tmp_path, "candidates/a.png", b"payload")
    document = {
        "lecture_id": "demo",
        "segments": [{"index": 1, "start_sec": 0, "end_sec": 60, "frames": []}],
    }
    candidates = [{
        "identity": "c1", "path": "candidates/a.png", "time": 30.0,
        "asset_sha256": digest, "quality": _quality(), "ocr": "slide text",
    }]

    curated, manifest = curate(document, candidates, tmp_path)

    assert document["segments"][0]["frames"] == []  # the source is not mutated
    frames = curated["segments"][0]["frames"]
    assert len(frames) == 1
    assert frames[0]["path"].startswith("frames/segment-001-01-")
    assert frames[0]["ocr"] == "slide text"
    assert (tmp_path / frames[0]["path"]).is_file()
    assert manifest["schema"] == "lecture-frame-curation-v1"
    assert manifest["segments"][0]["selected"][0]["asset_sha256"] == digest


def test_curate_refuses_a_segment_with_no_valid_candidate(tmp_path):
    document = {"segments": [{"index": 1, "start_sec": 0, "end_sec": 60, "frames": []}]}

    with pytest.raises(FrameCurationError, match="no_valid_frame"):
        curate(document, [], tmp_path)
