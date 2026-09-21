"""Task 5.4 -- staged candidate curation.

``--stage`` writes candidates to ``staging/frames/``; ``--curate`` is the gate
between that and the formal ``frames/`` directory. Every candidate is re-hashed
against the row capture wrote for it, so a file that changed after it was
measured cannot reach the document.

The acceptance case is one tampered staged file: it is rejected with reason
``hash mismatch``, nothing is copied for it, every other candidate is processed
exactly as it would have been, and the command exits 2.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PIL", reason="Pillow writes the staged candidate images")

from fixtures import synthetic  # noqa: E402

from lecture2notes import _deps  # noqa: E402
from lecture2notes.cli.main import main as cli_entry  # noqa: E402
from lecture2notes.frames import curation  # noqa: E402
from lecture2notes.frames.manifest import (  # noqa: E402
    read_manifest,
    record_for_file,
    write_manifest,
)
from lecture2notes.schema.io import read_json  # noqa: E402


@pytest.fixture()
def no_ffmpeg_needed(monkeypatch):
    """--curate decodes nothing, so the binary check is irrelevant to it."""
    monkeypatch.setattr(_deps, "require", lambda name, *a, **k: name)


def stage_candidates(base: Path, stem: str, seconds, shade=lambda n: 40 + n) -> list:
    """Write one staged PNG per second and return the manifest rows for them."""
    staging = base / "staging" / "frames"
    staging.mkdir(parents=True, exist_ok=True)
    rows = []
    for second in seconds:
        path = synthetic.gray_image(
            staging / ("%s-%02d%02d.png" % (stem, int(second) // 60, int(second) % 60)),
            shade(int(second)),
            size=(64, 36),
        )
        rows.append(record_for_file(float(second), path, base))
    return rows


def segment(index: int, start: float, end: float) -> dict:
    return {
        "index": index,
        "start_sec": float(start),
        "end_sec": float(end),
        "title": "segment %d" % index,
        "frame": None,
        "frames": [],
    }


# --------------------------------------------------------------------------
# the acceptance case
# --------------------------------------------------------------------------
def test_a_tampered_candidate_is_rejected_and_the_rest_are_promoted(tmp_path: Path):
    rows = stage_candidates(tmp_path, "talk", [0, 10, 20, 30])
    tampered = tmp_path / rows[1]["frame"]
    tampered.write_bytes(tampered.read_bytes() + b"tampered")

    result = curation.curate(rows, tmp_path, max_per_segment=4)

    assert [row["reason"] for row in result.rejected] == [curation.REASON_HASH_MISMATCH]
    assert result.rejected[0]["frame"] == "staging/frames/talk-0010.png"
    assert [row["frame"] for row in result.promoted] == [
        "frames/talk-0000.png", "frames/talk-0020.png", "frames/talk-0030.png",
    ]
    promoted_files = sorted(path.name for path in (tmp_path / "frames").iterdir())
    assert promoted_files == ["talk-0000.png", "talk-0020.png", "talk-0030.png"]
    assert result.blocked is True
    assert result.exit_code() == 2


def test_the_rejection_reason_string_is_the_contract_wording():
    assert curation.REASON_HASH_MISMATCH == "hash mismatch"


def test_a_promoted_copy_keeps_the_bytes_and_the_digest(tmp_path: Path):
    rows = stage_candidates(tmp_path, "talk", [0, 10])

    result = curation.curate(rows, tmp_path)

    for promoted, original in zip(result.promoted, rows):
        source = tmp_path / original["frame"]
        destination = tmp_path / promoted["frame"]
        assert destination.read_bytes() == source.read_bytes()
        assert promoted["sha256"] == original["sha256"]
        assert promoted["staged_from"] == original["frame"]


def test_a_missing_staged_file_is_rejected_and_blocks(tmp_path: Path):
    rows = stage_candidates(tmp_path, "talk", [0, 10])
    (tmp_path / rows[0]["frame"]).unlink()

    result = curation.curate(rows, tmp_path)

    assert [row["reason"] for row in result.rejected] == [curation.REASON_MISSING]
    assert result.exit_code() == 2
    assert len(result.promoted) == 1


def test_a_candidate_escaping_the_lecture_directory_is_rejected(tmp_path: Path):
    rows = stage_candidates(tmp_path, "talk", [0])
    rows.append({"timestamp_sec": 5.0, "frame": "../outside.png", "sha256": "0" * 64})

    result = curation.curate(rows, tmp_path)

    assert [row["reason"] for row in result.rejected] == [curation.REASON_UNSAFE_PATH]
    assert result.exit_code() == 2


# --------------------------------------------------------------------------
# the per-segment cap
# --------------------------------------------------------------------------
def test_the_cap_applies_per_segment_and_does_not_block(tmp_path: Path):
    rows = stage_candidates(tmp_path, "talk", [0, 5, 10, 15, 20, 25, 30, 35])
    document = {"segments": [segment(1, 0, 30), segment(2, 30, 60)]}

    result = curation.curate(rows, tmp_path, document=document, max_per_segment=4)

    promoted_by_segment = {}
    for row in result.promoted:
        promoted_by_segment.setdefault(row["segment"], []).append(row["frame"])
    assert len(promoted_by_segment[1]) == 4
    assert len(promoted_by_segment[2]) == 2
    assert [row["reason"] for row in result.rejected] == [curation.REASON_OVER_CAP] * 2
    # Being over the cap is the feature working, not a failure.
    assert result.blocked is False
    assert result.exit_code() == 0


def test_the_cap_is_configurable(tmp_path: Path):
    rows = stage_candidates(tmp_path, "talk", [0, 5, 10, 15])
    document = {"segments": [segment(1, 0, 30)]}

    result = curation.curate(rows, tmp_path, document=document, max_per_segment=2)

    assert len(result.promoted) == 2
    assert len(result.rejected) == 2


def test_a_tampered_candidate_does_not_consume_a_cap_slot(tmp_path: Path):
    """The remaining candidates must be processed exactly as they would have been."""
    rows = stage_candidates(tmp_path, "talk", [0, 5, 10, 15, 20])
    tampered = tmp_path / rows[0]["frame"]
    tampered.write_bytes(b"different bytes entirely")
    document = {"segments": [segment(1, 0, 30)]}

    result = curation.curate(rows, tmp_path, document=document, max_per_segment=4)

    assert [row["frame"] for row in result.promoted] == [
        "frames/talk-0005.png", "frames/talk-0010.png",
        "frames/talk-0015.png", "frames/talk-0020.png",
    ]
    assert [row["reason"] for row in result.rejected] == [curation.REASON_HASH_MISMATCH]


def test_without_a_document_every_candidate_shares_one_bucket(tmp_path: Path):
    rows = stage_candidates(tmp_path, "talk", [0, 5, 10, 15, 20])

    result = curation.curate(rows, tmp_path, document=None, max_per_segment=4)

    assert len(result.promoted) == 4
    assert [row["reason"] for row in result.rejected] == [curation.REASON_OVER_CAP]


def test_segment_lookup_uses_the_half_open_range():
    segments = [segment(1, 0, 30), segment(2, 30, 60)]

    assert curation.segment_of(segments, 0) == 1
    assert curation.segment_of(segments, 29.999) == 1
    assert curation.segment_of(segments, 30) == 2
    assert curation.segment_of(segments, 60) is None


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------
def test_the_report_lists_promoted_and_rejected_with_reasons(tmp_path: Path):
    rows = stage_candidates(tmp_path, "talk", [0, 10])
    tampered = tmp_path / rows[0]["frame"]
    tampered.write_bytes(b"tampered")

    report = curation.curate(rows, tmp_path).report()

    assert report["schema"] == curation.CURATION_SCHEMA
    assert report["promoted_count"] == 1
    assert report["rejected_count"] == 1
    assert report["rejected"][0]["reason"] == "hash mismatch"
    assert "manifest" in report["rejected"][0]["detail"]
    assert "actual" in report["rejected"][0]["detail"]
    # It has to survive a round trip: this is the artefact a human reads later.
    assert json.loads(json.dumps(report, ensure_ascii=False)) == report


# --------------------------------------------------------------------------
# the CLI, end to end
# --------------------------------------------------------------------------
def _prepare_lecture(tmp_path: Path, seconds=(0, 10, 20, 30)) -> Path:
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"placeholder; curate never decodes the video")
    rows = stage_candidates(tmp_path, "talk", seconds)
    write_manifest(tmp_path / "talk.frames.json", rows)
    (tmp_path / "talk.json").write_text(
        json.dumps({"schema_version": "2.0", "segments": [segment(1, 0, 40)]}, indent=2),
        encoding="utf-8",
    )
    return video


def test_cli_curate_exits_2_and_writes_the_curation_report(tmp_path: Path, no_ffmpeg_needed):
    video = _prepare_lecture(tmp_path)
    tampered = tmp_path / "staging" / "frames" / "talk-0010.png"
    tampered.write_bytes(tampered.read_bytes() + b"tampered")

    code = cli_entry(["frames", str(video), "--curate"])

    assert code == 2
    report = read_json(tmp_path / "talk.curation.json")
    assert report["rejected"][0]["reason"] == "hash mismatch"
    assert report["promoted_count"] == 3


def test_cli_curate_leaves_the_manifest_and_json_on_promoted_frames_only(
    tmp_path: Path, no_ffmpeg_needed
):
    video = _prepare_lecture(tmp_path)
    tampered = tmp_path / "staging" / "frames" / "talk-0010.png"
    tampered.write_bytes(b"tampered")

    cli_entry(["frames", str(video), "--curate"])

    manifest = read_manifest(tmp_path / "talk.frames.json")
    assert [row["frame"] for row in manifest] == [
        "frames/talk-0000.png", "frames/talk-0020.png", "frames/talk-0030.png",
    ]
    document = read_json(tmp_path / "talk.json")
    assert document["segments"][0]["frames"] == [
        "frames/talk-0000.png", "frames/talk-0020.png", "frames/talk-0030.png",
    ]
    assert document["segments"][0]["frame"] == "frames/talk-0000.png"
    # The staged copy that failed must not have reached the formal directory.
    assert not (tmp_path / "frames" / "talk-0010.png").exists()


def test_cli_curate_exits_0_when_every_candidate_verifies(tmp_path: Path, no_ffmpeg_needed):
    video = _prepare_lecture(tmp_path)

    code = cli_entry(["frames", str(video), "--curate"])

    assert code == 0
    assert read_json(tmp_path / "talk.curation.json")["rejected_count"] == 0


def test_cli_curate_without_a_manifest_is_a_usage_error(tmp_path: Path, no_ffmpeg_needed):
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"placeholder")

    assert cli_entry(["frames", str(video), "--curate"]) == 2
