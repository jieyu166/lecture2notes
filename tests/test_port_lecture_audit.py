"""Smoke tests for the structured audit ported from lecture_audit.py."""

from __future__ import annotations

import json

from lecture2notes.acceptance.audit import (
    AuditReport,
    audit_course,
    audit_lecture,
    canonical_snapshot,
    compare_derived,
    snapshot_from_payload,
    write_report,
)
from lecture2notes.outputs.pbf import pbf_text
from lecture2notes.schema.model import Finding


def _document():
    return {
        "stem": "demo",
        "title": "Demo",
        "segments": [
            {
                "index": 1,
                "start_sec": 0,
                "end_sec": 60,
                "title": "Opening",
                "summary_zh": "summary",
                "takeaways_zh": ["a", "b", "c", "d"],
                "editorial_notes_zh": [],
                "frames": [],
            }
        ],
    }


def test_canonical_snapshot_is_the_chapter_spine():
    assert canonical_snapshot(_document()) == [
        {"index": 1, "start_sec": 0.0, "end_sec": 60.0, "title": "Opening"}
    ]


def test_snapshot_from_payload_accepts_both_shapes():
    spine = canonical_snapshot(_document())
    segments = _document()["segments"]

    assert snapshot_from_payload(segments) == spine
    assert snapshot_from_payload({"segments": segments}) == spine
    assert snapshot_from_payload("not a snapshot") is None


def test_compare_derived_reports_a_missing_file(tmp_path):
    findings = compare_derived(_document(), pbf_path=tmp_path / "gone.pbf")

    assert [finding.code for finding in findings] == ["pbf_missing"]


def test_compare_derived_accepts_a_matching_chapter_file(tmp_path):
    document = _document()
    chapters = tmp_path / "demo.pbf"
    chapters.write_text(pbf_text(document), encoding="utf-8", newline="\n")

    assert compare_derived(document, pbf_path=chapters) == []

    chapters.write_text("[Bookmark]\n0=0*Different*\n", encoding="utf-8", newline="\n")
    assert [f.code for f in compare_derived(document, pbf_path=chapters)] == ["pbf_mismatch"]


def test_audit_report_counts_and_exit_code():
    report = AuditReport("demo", "audit", [
        Finding("error", "a", "a"),
        Finding("warning", "b", "b"),
    ])

    assert report.errors == 1 and report.warnings == 1
    assert report.ok is False
    assert report.exit_code() == 2
    assert AuditReport("demo", "audit", []).exit_code() == 0
    assert AuditReport("demo", "audit", [Finding("warning", "b", "b")]).exit_code() == 1


def test_audit_lecture_collects_schema_findings(tmp_path):
    report = audit_lecture(_document(), tmp_path, check_content=False)
    codes = {finding.code for finding in report.findings}

    assert report.lecture_id == "demo"
    assert "frame_count" in codes  # the segment has no frames
    assert report.to_dict()["summary"]["errors"] == report.errors


def test_write_report_records_the_guideline_version(tmp_path):
    report = audit_lecture(_document(), tmp_path, check_content=False)
    path = write_report(tmp_path / "demo.audit.json", report, guideline_version="1.0")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["guideline_version"] == "1.0"
    assert payload["summary"]["errors"] == report.errors
    assert path.read_bytes()[:3] != b"\xef\xbb\xbf"


def test_audit_course_rolls_up_failing_lecture_reports(tmp_path):
    good = tmp_path / "one.audit.json"
    bad = tmp_path / "two.audit.json"
    good.write_text(json.dumps({"lecture_id": "one", "ok": True}), encoding="utf-8")
    bad.write_text(json.dumps({"lecture_id": "two", "ok": False}), encoding="utf-8")

    report = audit_course([good, bad], expected_count=2)
    codes = [finding.code for finding in report.findings]

    assert codes == ["course_lecture_failed"]
    assert report.ok is False
