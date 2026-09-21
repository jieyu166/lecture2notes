"""Legacy 1.x documents are refused by `check json` and upgraded by `l2n migrate`.

The fixture is a synthetic 41-segment document: placeholder titles, placeholder
summaries and string bullets, in the 1.x shape (no schema_version, no source, a
top-level frame_ocr map). No real lecture content is involved.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from conftest import run_cli

from lecture2notes.acceptance.check import check_json
from lecture2notes.schema.migrate import migrate_file, normalize_legacy
from lecture2notes.schema.model import SCHEMA_VERSION, is_legacy, validate_document

FIXTURES = Path(__file__).parent / "fixtures"
LEGACY = FIXTURES / "lecture_legacy_41.json"
SEGMENT_COUNT = 41


def stage_legacy(tmp_path: Path, name: str = "legacy-talk.json") -> Path:
    """Copy the legacy fixture into ``tmp_path`` with the frames it references."""
    target = tmp_path / name
    shutil.copyfile(LEGACY, target)
    document = json.loads(target.read_text(encoding="utf-8"))
    for segment in document["segments"]:
        for frame in segment["frames"]:
            path = tmp_path / frame
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"placeholder frame")
    return target


def test_the_fixture_is_legacy_and_has_41_segments():
    document = json.loads(LEGACY.read_text(encoding="utf-8"))

    assert is_legacy(document)
    assert "schema_version" not in document
    assert len(document["segments"]) == SEGMENT_COUNT
    assert all(
        isinstance(bullet, str)
        for segment in document["segments"]
        for bullet in segment["bullets_zh"]
    )


def test_check_json_refuses_a_legacy_document(tmp_path):
    target = stage_legacy(tmp_path)

    report = check_json(target)
    assert [finding.code for finding in report.findings] == ["schema_version"]
    assert report.findings[0].message == (
        "legacy schema detected; run: l2n migrate legacy-talk.json"
    )
    assert report.exit_code() == 2


def test_cli_check_json_refuses_a_legacy_document(tmp_path):
    target = stage_legacy(tmp_path)

    result = run_cli(["check", "json", str(target)])
    assert result.returncode == 2
    assert "legacy schema detected; run: l2n migrate legacy-talk.json" in result.stdout
    assert "json: 1 errors, 0 warnings" in result.stdout


# -- the round trip ---------------------------------------------------------

def test_migrate_round_trip(tmp_path):
    """The scenario from the specification, assertion by assertion."""
    target = stage_legacy(tmp_path)
    original = target.read_bytes()

    result = migrate_file(target)
    assert result.changed is True

    backup = tmp_path / "legacy-talk.json.bak"
    assert backup.read_bytes() == original

    migrated = json.loads(target.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == SCHEMA_VERSION
    assert len(migrated["segments"]) == SEGMENT_COUNT
    assert all(
        isinstance(bullet, dict) and bullet["kind"] == "synthesis"
        for segment in migrated["segments"]
        for bullet in segment["bullets_zh"]
    )
    assert validate_document(migrated) == []
    assert check_json(target).exit_code() == 0


def test_cli_migrate_then_check_json_exits_0(tmp_path):
    target = stage_legacy(tmp_path)
    original = target.read_bytes()

    migrated = run_cli(["migrate", str(target)])
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    assert (tmp_path / "legacy-talk.json.bak").read_bytes() == original

    checked = run_cli(["check", "json", str(target)])
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "json: 0 errors, 0 warnings" in checked.stdout


def test_migrate_fills_in_every_missing_field(tmp_path):
    target = stage_legacy(tmp_path)
    migrate_file(target)
    migrated = json.loads(target.read_text(encoding="utf-8"))

    assert migrated["profile"] == "generic"
    assert migrated["title"] == "legacy-talk"
    assert migrated["source"] == {
        "video": "legacy-talk.mp4",
        "subtitle": {
            "path": "legacy-talk.srt",
            "origin": "asr",
            "engine": None,
            "lang": None,
            "offset_model": None,
        },
    }
    assert migrated["corrections"] == []
    assert migrated["unverified_terms"] == []
    for segment in migrated["segments"]:
        assert segment["quotes_zh"] == []
        assert segment["editorial_notes_zh"] == []
        assert isinstance(segment["frame_ocr"], list)


def test_migrate_keeps_the_times_and_the_frames(tmp_path):
    target = stage_legacy(tmp_path)
    before = json.loads(target.read_text(encoding="utf-8"))
    migrate_file(target)
    after = json.loads(target.read_text(encoding="utf-8"))

    assert [(s["start_sec"], s["end_sec"]) for s in after["segments"]] == [
        (s["start_sec"], s["end_sec"]) for s in before["segments"]
    ]
    assert [s["frame"] for s in after["segments"]] == [
        s["frame"] for s in before["segments"]
    ]
    assert [s["index"] for s in after["segments"]] == list(range(1, SEGMENT_COUNT + 1))


def test_migrate_folds_the_top_level_ocr_map_into_its_segment(tmp_path):
    target = stage_legacy(tmp_path)
    migrate_file(target)
    migrated = json.loads(target.read_text(encoding="utf-8"))

    assert "frame_ocr" not in migrated
    first = migrated["segments"][0]
    assert first["frame_ocr"] == [
        {"frame": "frames/legacy-talk-0001.png", "text": "佔位投影片文字 01"}
    ]
    assert migrated["segments"][1]["frame_ocr"] == []


def test_migrating_a_v2_document_changes_nothing(tmp_path):
    target = stage_legacy(tmp_path)
    migrate_file(target)
    upgraded = target.read_bytes()
    backup = (tmp_path / "legacy-talk.json.bak").read_bytes()

    again = migrate_file(target)
    assert again.changed is False
    assert again.backup is None
    assert target.read_bytes() == upgraded
    # The backup still holds the *original*, not the already-migrated copy.
    assert (tmp_path / "legacy-talk.json.bak").read_bytes() == backup


def test_cli_migrate_is_idempotent(tmp_path):
    target = stage_legacy(tmp_path)
    run_cli(["migrate", str(target)])
    first = target.read_bytes()

    result = run_cli(["migrate", str(target)])
    assert result.returncode == 0
    assert "already schema 2.0" in result.stdout
    assert target.read_bytes() == first


def test_cli_migrate_on_a_missing_file_exits_2(tmp_path):
    result = run_cli(["migrate", str(tmp_path / "nope.json")])
    assert result.returncode == 2


def test_cli_migrate_on_unparseable_json_exits_2(tmp_path):
    target = tmp_path / "broken.json"
    target.write_text("{not json", encoding="utf-8")

    result = run_cli(["migrate", str(target)])
    assert result.returncode == 2
    assert not (tmp_path / "broken.json.bak").exists()


# -- normalize_legacy in isolation -----------------------------------------

def test_string_bullets_become_synthesis_objects():
    upgraded = normalize_legacy(
        {
            "stem": "demo",
            "segments": [
                {"start": 0, "end": 60, "title": "t", "summary_zh": "s",
                 "bullets_zh": ["一", "二"]}
            ],
        },
        stem="demo",
    )

    assert upgraded["segments"][0]["bullets_zh"] == [
        {"text": "一", "t": None, "kind": "synthesis"},
        {"text": "二", "t": None, "kind": "synthesis"},
    ]


def test_an_already_objectified_bullet_keeps_its_time_and_kind():
    upgraded = normalize_legacy(
        {
            "stem": "demo",
            "segments": [
                {"start_sec": 0, "end_sec": 60, "title": "t", "summary_zh": "s",
                 "bullets_zh": [{"text": "一", "t": 12.5, "kind": "quote"}]}
            ],
        }
    )

    assert upgraded["segments"][0]["bullets_zh"] == [
        {"text": "一", "t": 12.5, "kind": "quote"}
    ]


def test_legacy_start_and_end_become_start_sec_and_end_sec():
    upgraded = normalize_legacy(
        {"stem": "demo", "segments": [{"start": 0, "end": 90, "title": "t"}]}
    )
    segment = upgraded["segments"][0]

    assert (segment["start_sec"], segment["end_sec"]) == (0.0, 90.0)
    assert (segment["start_time"], segment["end_time"]) == ("00:00:00", "00:01:30")
    assert "start" not in segment and "end" not in segment
    assert upgraded["duration_sec"] == 90.0


def test_frame_objects_become_paths_plus_a_frame_ocr_array():
    upgraded = normalize_legacy(
        {
            "stem": "demo",
            "segments": [
                {
                    "start": 0, "end": 60, "title": "t",
                    "frames": [{"path": "frames\\demo-0001.png", "time": 3.0,
                                "ocr": "佔位文字"}],
                }
            ],
        }
    )
    segment = upgraded["segments"][0]

    assert segment["frames"] == ["frames/demo-0001.png"]
    assert segment["frame"] == "frames/demo-0001.png"
    assert segment["frame_ocr"] == [
        {"frame": "frames/demo-0001.png", "text": "佔位文字"}
    ]


def test_a_segment_level_takeaways_list_is_read_as_bullets():
    """1.x normalisation renamed bullets_zh to takeaways_zh inside a segment."""
    upgraded = normalize_legacy(
        {
            "stem": "demo",
            "segments": [
                {"start": 0, "end": 60, "title": "t", "takeaways_zh": ["一"]}
            ],
        }
    )
    segment = upgraded["segments"][0]

    assert segment["bullets_zh"] == [{"text": "一", "t": None, "kind": "synthesis"}]
    assert "takeaways_zh" not in segment


def test_unknown_keys_survive_the_migration():
    upgraded = normalize_legacy(
        {
            "stem": "demo",
            "note_path": "demo.v4.md",
            "segments": [{"start": 0, "end": 60, "title": "t", "chapter": "A"}],
        }
    )

    assert upgraded["note_path"] == "demo.v4.md"
    assert upgraded["segments"][0]["chapter"] == "A"


def test_the_canonical_keys_come_first_and_in_order():
    upgraded = normalize_legacy(
        {"segments": [{"start": 0, "end": 60, "title": "t"}], "extra": 1}, stem="demo"
    )

    assert list(upgraded)[:5] == [
        "schema_version", "stem", "title", "duration_sec", "source"
    ]
    assert list(upgraded)[-1] == "extra"
