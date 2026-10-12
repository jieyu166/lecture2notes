"""Guideline 1.4: reader levels, the glossary skeleton, the review bundle, and
the three gaps the trial runs exposed (frames re-merge, editorial notes, OCR of
participant panels).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lecture2notes import exit_codes
from lecture2notes.cli.main import SUBCOMMANDS, main
from lecture2notes.frames.manifest import merge_frames_into_segments
from lecture2notes.notes import guideline, render
from lecture2notes.notes.render import render_skeleton
from lecture2notes.profiles import loader
from lecture2notes.schema.model import validate_document

FIXTURE = Path(__file__).parent / "fixtures" / "note_doc.json"
STEM = "20240115 demo talk"


def _document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture()
def lecture(tmp_path):
    document = _document()
    json_path = tmp_path / ("%s.json" % STEM)
    json_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "demo-0001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / ("%s.srt" % STEM)).write_text(
        "1\n00:00:05,000 --> 00:00:20,000\nonset in the sixth decade of life\n\n"
        "2\n00:01:10,000 --> 00:01:30,000\nremote history only\n\n",
        encoding="utf-8",
    )
    note_path = tmp_path / ("%s.v4.md" % STEM)
    note_path.write_text(
        render_skeleton(document, style="faithful", stem=STEM),
        encoding="utf-8", newline="\n",
    )
    return json_path, note_path


# --------------------------------------------------------------------------
# reader levels and the glossary skeleton (B14, B2)
# --------------------------------------------------------------------------
def test_the_default_reader_is_self_and_renders_no_glossary():
    assert loader.note_reader("generic") == "self"
    assert guideline.GLOSSARY_HEADING not in render_skeleton(_document(), stem=STEM)


@pytest.mark.parametrize("reader", ["clerk", "student", "public"])
def test_any_other_reader_renders_the_glossary_before_references(reader):
    with loader.cli_layer(note__reader=reader):
        text = render_skeleton(_document(), stem=STEM)

    assert text.index(guideline.GLOSSARY_HEADING) < text.index("### References")
    assert text.index("# Note (layer 1-3)") < text.index(guideline.GLOSSARY_HEADING)
    section = text.split(guideline.GLOSSARY_HEADING, 1)[1].split("### References")[0]
    assert render.AI_DRAFT_MARK in section
    assert "非講者" in guideline.GLOSSARY_HEADING


def test_the_glossary_lists_unverified_terms_then_abbreviations():
    document = _document()
    document["unverified_terms"] = ["shedding loss sign"]
    document["takeaways_zh"][0] = "MRI 與 CT 的取捨看 BI-RADS 分級"
    terms = render.glossary_candidates(document)

    assert terms[:4] == ["shedding loss sign", "MRI", "CT", "BI-RADS"]


def test_the_glossary_goes_into_a_radiology_note_after_the_reading_section():
    with loader.cli_layer(note__reader="student"):
        text = render_skeleton(_document(), stem=STEM, profile="radiology")

    assert text.index("# 閱片") < text.index(guideline.GLOSSARY_HEADING)
    assert text.index(guideline.GLOSSARY_HEADING) < text.index("### References")


def test_a_template_may_place_the_glossary_itself():
    template = "# Evergreen Note\n\n{{glossary}}\n\n### References\n\n{{references}}\n"
    with loader.cli_layer(note__reader="public"):
        text = render_skeleton(_document(), stem=STEM, template=template)

    assert text.count(guideline.GLOSSARY_HEADING) == 1
    assert text.index(guideline.GLOSSARY_HEADING) < text.index("### References")


def test_rendering_with_a_reader_is_still_deterministic():
    with loader.cli_layer(note__reader="clerk"):
        assert render_skeleton(_document(), stem=STEM) == render_skeleton(
            _document(), stem=STEM
        )


def _overlay(folder: Path, monkeypatch, body: str) -> None:
    """A project overlay: `main` installs its own command-line layer, so a
    setting a CLI test needs has to come from a file."""
    overlay = folder / ".lecture2notes"
    overlay.mkdir(exist_ok=True)
    (overlay / "outputs.toml").write_text(body, encoding="utf-8")
    monkeypatch.chdir(folder)


def test_the_expand_prompt_names_the_reader_level(lecture, capsys, monkeypatch):
    json_path, _ = lecture
    _overlay(json_path.parent, monkeypatch, '[note]\nreader = "student"\n')
    assert main(["render", "--expand-prompt", str(json_path)]) == exit_codes.OK
    out = capsys.readouterr().out

    assert "## 讀者層級：student（國高中生）" in out
    assert guideline.GLOSSARY_HEADING in out
    assert "不放寬「不可編造」" in out


def test_the_expand_prompt_for_self_says_so(lecture, capsys):
    json_path, _ = lecture
    assert main(["render", "--expand-prompt", str(json_path)]) == exit_codes.OK
    out = capsys.readouterr().out

    assert "## 讀者層級：self（筆記主人）" in out
    assert "HARD RULE 6" in out


def test_every_reader_level_is_in_the_guideline_table():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "note-writing-guideline.md")
    section = guideline.section_text(doc.read_text(encoding="utf-8"), "讀者層級")
    assert section, "the guideline has no reader-level section"
    for level, who, _terms, glossary, _questions in guideline.READER_GUIDANCE:
        row = next((line for line in section.splitlines()
                    if line.startswith("| `%s`" % level)), "")
        assert who in row and glossary in row, level
    assert tuple(level for level, *_ in guideline.READER_GUIDANCE) == loader.READER_LEVELS


# --------------------------------------------------------------------------
# B6: the two References switches
# --------------------------------------------------------------------------
def test_the_reference_switches_are_off_by_default(lecture, capsys):
    json_path, _ = lecture
    assert loader.verify_citations("generic") is False
    assert loader.record_handout("generic") is False
    main(["render", "--expand-prompt", str(json_path)])
    out = capsys.readouterr().out

    assert "CrossRef" not in out
    assert guideline.RECORD_HANDOUT_STEP not in out
    assert guideline.VERIFY_CITATIONS_STEP not in out


def test_each_switch_adds_its_step(lecture, capsys, monkeypatch):
    json_path, _ = lecture
    _overlay(json_path.parent, monkeypatch,
             "[references]\nverify_citations = true\nrecord_handout = true\n")
    main(["render", "--expand-prompt", str(json_path)])
    out = capsys.readouterr().out

    assert guideline.VERIFY_CITATIONS_STEP in out
    assert guideline.RECORD_HANDOUT_STEP in out
    assert "unverified_terms" in guideline.VERIFY_CITATIONS_STEP


def test_the_bundle_points_at_the_independent_review(lecture, capsys):
    json_path, note_path = lecture
    main(["render", "--expand-prompt", str(json_path)])

    assert "l2n review-prompt %s --note %s" % (json_path, note_path) in (
        capsys.readouterr().out
    )


# --------------------------------------------------------------------------
# top-level editorial_notes_zh (trial-run gap 2)
# --------------------------------------------------------------------------
def test_top_level_editorial_notes_are_optional_and_validated():
    document = _document()
    assert not [f for f in validate_document(document) if f.code == "editorial_notes_zh"]

    document["editorial_notes_zh"] = ["四段弧：坡道段 1、正文段 2，缺昇華"]
    assert not [f for f in validate_document(document) if f.code == "editorial_notes_zh"]

    document["editorial_notes_zh"] = "not a list"
    assert [f.severity for f in validate_document(document)
            if f.code == "editorial_notes_zh"] == ["error"]


def test_render_prints_the_editorial_notes_in_summary():
    document = _document()
    document["editorial_notes_zh"] = ["四段弧：缺昇華，結尾被 Q&A 吃掉"]
    text = render_skeleton(document, stem=STEM)
    summary = text.split("# Summary", 1)[1].split("## 講者骨架", 1)[0]

    assert render.EDITORIAL_CALLOUT in summary
    assert "> - 四段弧：缺昇華，結尾被 Q&A 吃掉" in summary


def test_no_editorial_notes_means_no_callout():
    assert render.EDITORIAL_CALLOUT not in render_skeleton(_document(), stem=STEM)


# --------------------------------------------------------------------------
# frames re-merge keeps a chosen frame (trial-run gap 1)
# --------------------------------------------------------------------------
def _row(second: float, name: str) -> dict:
    return {"timestamp_sec": float(second), "frame": "frames/%s" % name}


def test_a_chosen_frame_survives_a_re_merge(tmp_path):
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "picked-by-eye.png").write_bytes(b"png")
    document = {"segments": [
        {"index": 1, "start_sec": 0.0, "end_sec": 60.0,
         "frame": "frames/picked-by-eye.png", "frames": []},
        {"index": 2, "start_sec": 60.0, "end_sec": 120.0, "frame": None, "frames": []},
        {"index": 3, "start_sec": 120.0, "end_sec": 180.0,
         "frame": "frames/a.png", "frames": []},
    ]}
    merged = merge_frames_into_segments(
        document, [_row(5, "a.png"), _row(70, "b.png"), _row(130, "c.png")],
        base_dir=tmp_path,
    )

    assert merged["segments"][0]["frame"] == "frames/picked-by-eye.png"
    assert merged["segments"][0]["frames"] == ["frames/a.png"]
    assert merged["segments"][1]["frame"] == "frames/b.png"
    # still listed by the capture, so still a frame, though not this segment's own
    assert merged["segments"][2]["frame"] == "frames/a.png"


def test_a_kept_frame_that_no_longer_exists_is_filled_again(tmp_path):
    """An interval re-run may delete the old lead frame; keeping it would point
    the segment at nothing until R2 noticed."""
    document = {"segments": [
        {"index": 1, "start_sec": 0.0, "end_sec": 60.0,
         "frame": "frames/deleted-by-rerun.png", "frames": []},
    ]}
    merged = merge_frames_into_segments(document, [_row(5, "a.png")], base_dir=tmp_path)

    assert merged["segments"][0]["frame"] == "frames/a.png"


def test_force_puts_every_lead_frame_back():
    document = {"segments": [
        {"index": 1, "start_sec": 0.0, "end_sec": 60.0,
         "frame": "frames/picked-by-eye.png", "frames": []},
    ]}
    merged = merge_frames_into_segments(document, [_row(5, "a.png")], keep_existing=False)

    assert merged["segments"][0]["frame"] == "frames/a.png"


def test_the_frames_command_passes_force_through(monkeypatch, tmp_path):
    from lecture2notes import _deps
    from lecture2notes.frames import capture as capture_mod

    seen = {}

    class Result:
        kept = True
        manifest_path = tmp_path / "x.frames.json"

    def fake_capture(source, **kwargs):
        seen.update(kwargs)
        return Result()

    video = tmp_path / "x.mp4"
    video.write_bytes(b"0")
    monkeypatch.setattr(_deps, "require", lambda name: None)
    monkeypatch.setattr(capture_mod, "capture", fake_capture)
    assert main(["frames", str(video)]) == exit_codes.OK
    assert seen["force"] is False
    assert main(["frames", str(video), "--force"]) == exit_codes.OK
    assert seen["force"] is True


# --------------------------------------------------------------------------
# l2n review-prompt (B7 (c))
# --------------------------------------------------------------------------
def test_review_prompt_is_the_eighteenth_subcommand():
    assert "review-prompt" in SUBCOMMANDS
    assert len(SUBCOMMANDS) == 18


def _write_body(note_path: Path, line: str) -> None:
    text = note_path.read_text(encoding="utf-8")
    text = text.replace("佔位段落摘要一。", line, 1)
    note_path.write_text(text, encoding="utf-8", newline="\n")


def test_review_prompt_prints_the_brief(lecture, capsys):
    json_path, note_path = lecture
    _write_body(note_path, "病灶 3 cm 以上占 40%，講者說「約十分之一會增強」(00:00:30)")
    assert main(["review-prompt", str(json_path)]) == exit_codes.OK
    out = capsys.readouterr().out

    assert out.startswith(guideline.VERSION_LINE)
    for name, _what in guideline.REVIEW_ERRORS:
        assert name in out, name
    # numbers, with the segment they belong to and its time span
    assert "段 1（00:00:00 - 00:01:00）" in out and "3 cm" in out and "40%" in out
    # the decade mention in the transcript
    assert "sixth decade" in out
    # quotations, with their timecodes
    assert "(00:00:30): 「約十分之一會增強」" in out
    # the report format
    assert "PASS" in out and "需修正" in out and "依嚴重度排序" in out
    assert "grep" in out


def test_review_prompt_lists_r12_context_hits_for_radiology(lecture, capsys):
    json_path, note_path = lecture
    document = json.loads(json_path.read_text(encoding="utf-8"))
    document["profile"] = "radiology"
    json_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    text = note_path.read_text(encoding="utf-8").replace(
        "佔位段落摘要二。", "病人只有遠端的病史。", 1
    )
    note_path.write_text(text, encoding="utf-8", newline="\n")

    assert main(["review-prompt", str(json_path)]) == exit_codes.OK
    out = capsys.readouterr().out
    section = out.split("R12 context", 1)[1]
    assert "「遠端」 -> 「久遠」" in section


def test_review_prompt_samples_at_least_eight_quotations(lecture):
    from lecture2notes.notes import review

    lines = ["「第%d句」(00:00:%02d)" % (n, n) for n in range(30)]
    total, sample = review.quotation_sample(lines)

    assert total == 30
    assert len(sample) >= review.QUOTE_SAMPLE
    assert sample[0].startswith("- L1 ")


def test_review_prompt_without_the_note_is_a_usage_error(lecture, capsys):
    json_path, note_path = lecture
    note_path.unlink()

    assert main(["review-prompt", str(json_path)]) == exit_codes.ERROR


def test_review_prompt_writes_nothing(lecture, capsys):
    json_path, note_path = lecture
    before = sorted((p.name, p.stat().st_mtime_ns) for p in json_path.parent.iterdir())
    main(["review-prompt", str(json_path), "--note", str(note_path)])

    assert sorted((p.name, p.stat().st_mtime_ns)
                  for p in json_path.parent.iterdir()) == before


def test_review_prompt_survives_cp950(lecture):
    from conftest import run_cli

    json_path, _ = lecture
    proc = run_cli(["review-prompt", str(json_path)],
                   env={"PYTHONIOENCODING": "cp950"}, encoding="cp950")
    assert proc.returncode == 0, proc.stderr
    assert "UnicodeEncodeError" not in proc.stderr
