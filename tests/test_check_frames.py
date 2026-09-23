"""`l2n check frames <stem>.frames.json` -- the frames stage acceptance check.

Three rules: every listed file exists and still hashes to the value the manifest
recorded, timestamps strictly increase, and there is at least one frame. The
hash rule is why the manifest carries ``sha256`` at all -- a frame regenerated
after the manifest was written looks perfectly fine on disk and silently
un-anchors every reference to it.

The scenario this pins is a hash drift reporting
``error sha256 frames/x-0512.png: manifest 3f2a.. actual 91cc..`` and exiting 2.

No ffmpeg here: the check reads a manifest and hashes files, and a test that
needed a video to verify a JSON rule would be testing the wrong thing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PIL", reason="Pillow writes the frames being hashed")

from fixtures import synthetic  # noqa: E402

from lecture2notes.acceptance.check import check_frames  # noqa: E402
from lecture2notes.cli.main import main as cli_entry  # noqa: E402
from lecture2notes.frames.manifest import record_for_file, write_manifest  # noqa: E402


def build_manifest(tmp_path: Path, seconds=(0, 312, 600), stem: str = "x") -> Path:
    frames_dir = tmp_path / "frames"
    rows = []
    for index, second in enumerate(seconds):
        name = "%s-%02d%02d.png" % (stem, int(second) // 60, int(second) % 60)
        path = synthetic.gray_image(frames_dir / name, 30 + index * 30, size=(64, 36))
        rows.append(record_for_file(float(second), path, tmp_path))
    return write_manifest(tmp_path / ("%s.frames.json" % stem), rows)


def test_a_clean_manifest_is_clean(tmp_path: Path):
    manifest = build_manifest(tmp_path)

    report = check_frames(manifest)

    assert report.findings == []
    assert report.exit_code() == 0
    assert report.summary_line() == "frames: 0 errors, 0 warnings"


def test_a_regenerated_frame_is_reported_as_a_hash_drift(tmp_path: Path):
    manifest = build_manifest(tmp_path)
    drifted = tmp_path / "frames" / "x-0512.png"
    synthetic.gray_image(drifted, 200, size=(64, 36))

    report = check_frames(manifest)
    lines = report.lines()

    assert len(lines) == 1
    assert lines[0].startswith("error sha256 frames/x-0512.png: manifest ")
    assert " actual " in lines[0]
    # <severity> <rule-or-field> <location>: <message>, with four-character
    # digest previews -- exactly the shape the acceptance scenario prints.
    severity, rule, rest = lines[0].split(" ", 2)
    location, message = rest.split(": ", 1)
    assert (severity, rule, location) == ("error", "sha256", "frames/x-0512.png")
    words = message.split()
    assert words[0] == "manifest" and words[2] == "actual"
    assert words[1].endswith("..") and len(words[1]) == 6
    assert words[3].endswith("..") and len(words[3]) == 6
    assert report.exit_code() == 2


def test_a_missing_frame_file_is_an_error(tmp_path: Path):
    manifest = build_manifest(tmp_path)
    (tmp_path / "frames" / "x-0512.png").unlink()

    report = check_frames(manifest)

    assert [finding.code for finding in report.findings] == ["frame_missing"]
    assert report.exit_code() == 2


def test_timestamps_must_strictly_increase(tmp_path: Path):
    manifest = build_manifest(tmp_path)
    rows = json.loads(manifest.read_text(encoding="utf-8"))
    rows[2]["timestamp_sec"] = rows[1]["timestamp_sec"]
    manifest.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    report = check_frames(manifest)

    assert [finding.code for finding in report.findings] == ["timestamp"]
    assert report.exit_code() == 2


def test_an_empty_manifest_is_an_error(tmp_path: Path):
    manifest = write_manifest(tmp_path / "x.frames.json", [])

    report = check_frames(manifest)

    assert [finding.code for finding in report.findings] == ["frames"]
    assert "no frames" in report.findings[0].message
    assert report.exit_code() == 2


def test_a_frame_path_escaping_the_lecture_directory_is_an_error(tmp_path: Path):
    manifest = write_manifest(
        tmp_path / "x.frames.json",
        [{"timestamp_sec": 0.0, "frame": "../secrets.png", "sha256": "0" * 64}],
    )

    report = check_frames(manifest)

    assert [finding.code for finding in report.findings] == ["frame_path"]
    assert report.exit_code() == 2


def test_a_row_without_a_digest_is_only_a_warning(tmp_path: Path):
    build_manifest(tmp_path, seconds=(0,))
    rows = json.loads((tmp_path / "x.frames.json").read_text(encoding="utf-8"))
    rows[0].pop("sha256")
    manifest = tmp_path / "x.frames.json"
    manifest.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    report = check_frames(manifest)

    assert [finding.severity for finding in report.findings] == ["warn"]
    assert report.exit_code() == 1
    assert report.summary_line() == "frames: 0 errors, 1 warnings"


def test_an_unreadable_manifest_is_reported_not_raised(tmp_path: Path):
    manifest = tmp_path / "x.frames.json"
    manifest.write_text("{not json", encoding="utf-8")

    report = check_frames(manifest)

    assert [finding.code for finding in report.findings] == ["parse"]
    assert report.exit_code() == 2


# --------------------------------------------------------------------------
# through the CLI
# --------------------------------------------------------------------------
def test_cli_check_frames_prints_the_summary_and_exits_2(tmp_path: Path, capsys):
    manifest = build_manifest(tmp_path)
    synthetic.gray_image(tmp_path / "frames" / "x-0512.png", 200, size=(64, 36))

    code = cli_entry(["check", "frames", str(manifest)])

    printed = capsys.readouterr().out
    assert code == 2
    assert "error sha256 frames/x-0512.png: manifest " in printed
    assert "frames: 1 errors, 0 warnings" in printed


def test_cli_check_frames_exits_0_on_a_clean_manifest(tmp_path: Path, capsys):
    manifest = build_manifest(tmp_path)

    code = cli_entry(["check", "frames", str(manifest)])

    assert code == 0
    assert "frames: 0 errors, 0 warnings" in capsys.readouterr().out


def test_cli_check_frames_without_a_target_is_a_usage_error():
    assert cli_entry(["check", "frames"]) == 2


# --------------------------------------------------------------------------
# the formal document, which is what SKILL.md tells people to pass
# --------------------------------------------------------------------------
def build_document(tmp_path: Path, stem: str = "x") -> Path:
    """A minimal v2 document whose two segments reference the manifest's frames."""
    manifest = build_manifest(tmp_path, stem=stem)
    rows = json.loads(manifest.read_text(encoding="utf-8"))
    document = {
        "schema_version": "2.0",
        "stem": stem,
        "segments": [
            {"index": 1, "start_sec": 0, "end_sec": 312,
             "frame": rows[0]["frame"], "frames": [rows[0]["frame"]]},
            {"index": 2, "start_sec": 312, "end_sec": 900,
             "frame": rows[1]["frame"], "frames": [rows[1]["frame"], rows[2]["frame"]]},
        ],
    }
    path = tmp_path / ("%s.json" % stem)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def test_the_formal_document_is_accepted_and_clean(tmp_path: Path):
    """`l2n check frames <stem>.json` is what SKILL.md documents, so it has to
    work: it used to exit 2 with "manifest lists no frames"."""
    document = build_document(tmp_path)

    report = check_frames(document)

    assert report.findings == [], report.lines()
    assert report.exit_code() == 0


def test_the_formal_document_reports_a_frame_that_is_gone(tmp_path: Path):
    document = build_document(tmp_path)
    (tmp_path / "frames" / "x-0000.png").unlink()

    report = check_frames(document)

    assert "frame_missing" in [finding.code for finding in report.findings]
    assert report.exit_code() == 2


def test_the_document_still_gets_the_manifest_hash_rule(tmp_path: Path):
    """The sibling manifest is checked too, so passing the document is not a
    weaker check than passing the manifest."""
    document = build_document(tmp_path)
    synthetic.gray_image(tmp_path / "frames" / "x-0512.png", 200, size=(64, 36))

    report = check_frames(document)

    assert "sha256" in [finding.code for finding in report.findings]
    assert report.exit_code() == 2
