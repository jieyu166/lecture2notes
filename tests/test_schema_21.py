"""Schema 2.1: lines, per-segment question and triage, triage_overall.

Every test mutates the valid 2.0 fixture, so it can only fail for the reason it
is about. All text is placeholder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import run_cli

from lecture2notes.acceptance.check import check_json
from lecture2notes.schema.io import write_json_atomic
from lecture2notes.schema.migrate import migrate_file
from lecture2notes.schema.model import SCHEMA_VERSION, validate_document

FIXTURES = Path(__file__).parent / "fixtures"
VALID = FIXTURES / "lecture_v2_valid.json"

GOOD_REASON = "只需看第 2 段（共 1 分鐘，省下 2 分鐘）：第 2 段的判準 vault 沒有，其餘已由筆記涵蓋。"


def base() -> dict:
    document = json.loads(VALID.read_text(encoding="utf-8"))
    document["schema_version"] = "2.1"
    return document


def full() -> dict:
    """A 2.1 document using every new field correctly."""
    document = base()
    document["lines"] = [
        {"t": 0.5, "end": 4.0, "text": "line one"},
        {"t": 70.0, "end": 75.0, "text": "line two", "zh": "第二句"},
        {"t": 130.0, "end": 178.0, "text": "line three"},
    ]
    for segment, at in zip(document["segments"], (2.0, 72.0, 140.0)):
        segment["question"] = {"q": "placeholder question", "a": "placeholder answer", "answer_sec": at}
        segment["triage"] = {"novelty": "新知", "reason": "placeholder reason", "related_notes": []}
    document["segments"][0]["triage"] = {
        "novelty": "已知", "reason": "covered", "related_notes": ["[[Placeholder Note]]"]
    }
    document["triage_overall"] = {
        "verdict": "只看新知段", "must_watch": [2], "est_minutes": 1, "reason": GOOD_REASON,
    }
    return document


def found(document: dict, severity: str = "error") -> list:
    return [f.code for f in validate_document(document) if f.severity == severity]


def test_full_21_document_is_clean():
    assert validate_document(full()) == []


def test_20_document_without_new_fields_is_still_valid():
    document = base()
    document["schema_version"] = "2.0"
    assert validate_document(document) == []


def test_unknown_version_names_upgrade():
    document = base()
    document["schema_version"] = "2.2"
    messages = [f.message for f in validate_document(document) if f.code == "schema_version"]
    assert messages and "upgrade" in messages[0]


def test_20_document_with_21_fields_is_an_error():
    document = full()
    document["schema_version"] = "2.0"
    findings = [f for f in validate_document(document) if f.code == "schema_version"]
    assert findings and "l2n migrate" in findings[0].message
    assert "lines" in findings[0].message and "segments[].question" in findings[0].message


# -- lines ------------------------------------------------------------------

def test_lines_out_of_order():
    document = full()
    document["lines"][1]["t"] = 0.1
    assert "lines_order" in found(document)


def test_lines_end_before_last_segment_is_truncated():
    document = full()
    document["lines"] = document["lines"][:2]
    assert "lines_coverage" in found(document)


def test_lines_late_start_warns():
    document = full()
    document["lines"][0] = {"t": 65.0, "end": 66.0, "text": "late"}
    document["lines"][1]["t"] = 70.0
    assert "lines_coverage" in found(document, "warn")


def test_lines_past_duration_is_an_error():
    document = full()
    document["lines"][-1]["end"] = 400.0
    assert "lines_range" in found(document)


def test_lines_zh_must_be_string():
    document = full()
    document["lines"][0]["zh"] = 3
    assert "lines" in found(document)


# -- question ---------------------------------------------------------------

def test_answer_sec_outside_segment():
    document = full()
    document["segments"][1]["question"]["answer_sec"] = 10.0
    assert "answer_sec" in found(document)


def test_answer_without_nearby_line_warns():
    document = full()
    document["segments"][1]["question"]["answer_sec"] = 100.0
    assert "answer_unsupported" in found(document, "warn")


def test_question_needs_q_and_a():
    document = full()
    document["segments"][0]["question"]["a"] = " "
    assert "question" in found(document)


def test_some_segments_without_question_warn():
    document = full()
    del document["segments"][2]["question"]
    assert "question_missing" in found(document, "warn")


# -- triage -----------------------------------------------------------------

def test_known_segment_must_link_a_note():
    document = full()
    document["segments"][0]["triage"]["related_notes"] = []
    assert "triage" in found(document)


def test_related_notes_must_be_wikilinks():
    document = full()
    document["segments"][0]["triage"]["related_notes"] = ["Placeholder Note"]
    assert "triage" in found(document)


def test_bad_novelty():
    document = full()
    document["segments"][1]["triage"]["novelty"] = "new"
    assert "triage" in found(document)


@pytest.mark.parametrize("reason", [
    "內容實用，值得一看。",
    "這場講座內容非常實用，建議花時間把新知的部分好好看完一遍，收穫會很多。",
])
def test_generic_reason_is_rejected(reason):
    document = full()
    document["triage_overall"]["reason"] = reason
    assert "triage_vague" in found(document)


def test_skip_needs_minutes_but_no_segment():
    document = full()
    document["triage_overall"] = {
        "verdict": "跳過", "must_watch": [], "est_minutes": 0,
        "reason": "全片內容皆已由既有筆記涵蓋，直接跳過可省下 3 分鐘，沒有新的判準或數值。",
    }
    assert "triage_vague" not in found(document)


def test_watch_all_needs_segment_but_no_minutes():
    document = full()
    document["triage_overall"] = {
        "verdict": "全看", "must_watch": [1, 2, 3], "est_minutes": 3,
        "reason": "第 1 至 3 段環環相扣，每段都有 vault 裡沒有的新判準，不能只挑一段看。",
    }
    assert "triage_vague" not in found(document)


def test_vague_reason_is_only_a_warning_in_a_draft():
    document = full()
    document["draft"] = True
    document["triage_overall"]["reason"] = "值得看"
    assert "triage_vague" not in found(document)
    assert "triage_vague" in found(document, "warn")


def test_must_watch_unknown_segment():
    document = full()
    document["triage_overall"]["must_watch"] = [9]
    assert "triage_overall" in found(document)


def test_only_new_needs_must_watch():
    document = full()
    document["triage_overall"]["must_watch"] = []
    assert "triage_overall" in found(document)


def test_est_minutes_disagreeing_with_must_watch_warns():
    document = full()
    document["triage_overall"]["est_minutes"] = 2.0
    document["triage_overall"]["must_watch"] = [2]
    document["duration_sec"] = 180
    document["segments"][1]["end_sec"] = 120
    assert "triage_minutes" not in found(document, "warn")
    document["triage_overall"]["est_minutes"] = 3.5
    assert "triage_minutes" in found(document, "warn")


def test_must_watch_known_segment_warns():
    document = full()
    document["triage_overall"]["must_watch"] = [1, 2]
    document["triage_overall"]["est_minutes"] = 2
    assert "triage_conflict" in found(document, "warn")


def test_triage_without_overall_warns():
    document = full()
    del document["triage_overall"]
    assert "triage_missing" in found(document, "warn")


# -- migrate ------------------------------------------------------------------

def test_migrate_relabels_20_without_touching_content(tmp_path):
    document = base()
    document["schema_version"] = "2.0"
    target = write_json_atomic(tmp_path / "talk.json", document)
    result = migrate_file(target)
    assert result.changed and result.backup.exists()
    upgraded = json.loads(target.read_text(encoding="utf-8"))
    assert upgraded["schema_version"] == SCHEMA_VERSION
    upgraded["schema_version"] = "2.0"
    assert upgraded == document


# -- check json / l2n lines -------------------------------------------------

SRT = (
    "1\n00:00:00,500 --> 00:00:04,000\nline one\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nline one\n\n"
    "3\n00:01:10,000 --> 00:01:15,000\nline two\n\n"
    "4\n00:02:10,000 --> 00:02:58,000\nline three\n"
)


def _write(tmp_path: Path, document: dict) -> Path:
    for segment in document["segments"]:
        for frame in list(segment.get("frames") or []) + [segment.get("frame")]:
            if frame:
                (tmp_path / frame).parent.mkdir(parents=True, exist_ok=True)
                (tmp_path / frame).write_bytes(b"x")
    (tmp_path / "sample-talk.srt").write_text(SRT, encoding="utf-8")
    return write_json_atomic(tmp_path / "sample-talk.json", document)


def test_cli_lines_writes_relabels_and_keeps_translations(tmp_path):
    document = base()
    document["schema_version"] = "2.0"
    path = _write(tmp_path, document)

    result = run_cli(["lines", str(path)])
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["lines"] == [
        {"t": 0.5, "end": 6.0, "text": "line one"},
        {"t": 70.0, "end": 75.0, "text": "line two"},
        {"t": 130.0, "end": 178.0, "text": "line three"},
    ]
    assert check_json(path).findings == []

    data["lines"][1]["zh"] = "第二句"
    write_json_atomic(path, data)
    (tmp_path / "sample-talk.srt").write_text(SRT.replace("line three", "line 3"), encoding="utf-8")
    assert "lines_stale" in [f.code for f in check_json(path).findings]

    assert "已有 lines" in run_cli(["lines", str(path)]).stdout
    assert run_cli(["lines", str(path), "--force"]).returncode == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["lines"][1]["zh"] == "第二句"
    assert data["lines"][2] == {"t": 130.0, "end": 178.0, "text": "line 3"}


def test_cli_lines_refuses_legacy(tmp_path):
    legacy = json.loads((FIXTURES / "lecture_legacy_41.json").read_text(encoding="utf-8"))
    path = write_json_atomic(tmp_path / "legacy-talk.json", legacy)
    result = run_cli(["lines", str(path)])
    assert result.returncode == 2
    assert "migrate" in result.stdout + result.stderr


def test_migrate_refuses_a_json_that_is_not_a_lecture(tmp_path):
    target = tmp_path / "E1.timing-fixes.json"
    original = '{"fixes": [{"cue": 3, "shift": 0.5}]}'
    target.write_text(original, encoding="utf-8")
    result = run_cli(["migrate", str(target)])
    assert result.returncode == 2
    assert "not a lecture" in result.stdout + result.stderr
    assert target.read_text(encoding="utf-8") == original
    assert not (tmp_path / "E1.timing-fixes.json.bak").exists()


def test_migrate_repairs_a_truncated_duration_and_odd_bullet_kinds(tmp_path):
    import shutil

    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe not installed")
    shutil.copy(Path(__file__).parent / "fixtures" / "media" / "copyrightx-12-1-clip.mp4", tmp_path / "clip.mp4")
    document = base()
    document["source"]["video"] = "clip.mp4"
    document["duration_sec"] = 23
    document["segments"] = document["segments"][:1]
    document["segments"][0].update({"end_sec": 23, "end_time": "00:00:23"})
    document["segments"][0]["bullets_zh"][0]["kind"] = "speaker"
    target = write_json_atomic(tmp_path / "clip.json", document)

    result = migrate_file(target)
    assert result.changed and result.backup.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["duration_sec"] == 25.025
    assert data["segments"][-1]["end_sec"] == 25 and data["segments"][-1]["end_time"] == "00:00:25"
    assert data["segments"][0]["bullets_zh"][0]["kind"] == "synthesis"
    assert any("ffprobe" in note for note in result.repairs)
    assert not migrate_file(target).changed


@pytest.mark.parametrize("name", ["talk.frames.json", "talk.corrections.json", "_titles.json"])
def test_check_json_skips_sidecars(tmp_path, name):
    target = tmp_path / name
    target.write_text('{"frames": []}', encoding="utf-8")
    report = check_json(target)
    assert [f.code for f in report.findings] == ["not_lecture"]
    assert report.findings[0].severity == "info"
    result = run_cli(["check", "json", str(target)])
    assert result.returncode == 0, result.stdout


def test_numbers_in_a_summary_are_checked_against_the_segment(tmp_path):
    from lecture2notes.notes import rules

    document = full()
    document["lines"] = [
        {"t": 1.0, "end": 4.0, "text": "about 35.5 percent"},
        {"t": 70.0, "end": 75.0, "text": "we follow up at 12 months"},
        {"t": 130.0, "end": 178.0, "text": "nothing numeric here"},
    ]
    document["segments"][0]["summary_zh"] = "約 35.5% 會增強。"
    document["segments"][2]["summary_zh"] = "12 個月追蹤；AFP 400 以上要注意；腫瘤 20 mm。"
    document["segments"][2]["frame_ocr"] = [{"frame": "frames/x.png", "text": "AFP > 400 ng/mL"}]
    from lecture2notes.acceptance.check import spoken_by_segment

    found = rules.segment_number_findings(document, spoken_by_segment(document, None))
    messages = [f.message for f in found]
    assert not any("35.5" in m for m in messages)
    assert "segment 3: 12 is said in segment 2, not in this one" in messages
    assert "segment 3: 400 is on the segment's slide but the speaker does not say it" in messages
    assert "segment 3: 20 is not in this segment's transcript or slides" in messages


def test_number_check_ignores_timecodes_years_and_declared_slide_values():
    from lecture2notes.notes import rules

    assert rules.claimed_numbers(
        "講者口述 AFP 正常（00:11:21）；投影片列 AFP 3.4（投影片）；Radiology 2018 指出 15 cm；"
        "[▶ 01:02](http://media.example/a.mp4#t=62) 腫瘤 20 mm"
    ) == ["15", "20"]


def test_spoken_numbers_in_words_count_as_said():
    from lecture2notes.notes import rules

    text = rules.spoken_numbers_text("大概十五公分，三點四倍，一百二十秒；twenty five patients, two hundred cases")
    for value in ("15", "3.4", "120", "25", "200"):
        assert value in rules._numbers(text), value


def test_a_bullet_that_names_a_slide_covers_all_its_numbers():
    from lecture2notes.notes import rules

    bullet = "（投影片）1 year：clinical success 72%（18/25）；bleeding score 5.5→2.8。"
    assert rules.claimed_numbers(bullet, whole=True) == []
    assert rules.claimed_numbers("講者說 12 個月；投影片列 72%", whole=False) == ["12"]
