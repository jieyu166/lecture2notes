"""Guideline 1.4: the Taiwanese translation list, for people and for the check.

The list lives twice by design (B8 (e)): appendix C of the guideline is the
copy a person reads, ``profiles/radiology/outputs.toml`` is the copy R12 and
``check json`` run. Two copies drift unless something compares them, so this
file does.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lecture2notes.acceptance import check
from lecture2notes.notes import guideline, rules
from lecture2notes.profiles import loader
from lecture2notes.profiles.layers import read_toml

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "note-writing-guideline.md"
RADIOLOGY_OUTPUTS = (
    REPO_ROOT / "src" / "lecture2notes" / "profiles" / "radiology" / "outputs.toml"
)
APPENDIX_HEADING = "附錄 C：台灣譯名對照表"


def _appendix_rows():
    section = guideline.section_text(DOC.read_text(encoding="utf-8"), APPENDIX_HEADING)
    assert section, "the guideline has no appendix C"
    rows = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4 or not line.strip().startswith("|"):
            continue
        if cells[0] in ("錯譯",) or set(cells[0]) <= set("-: "):
            continue
        rows[cells[0]] = (cells[1], cells[3])
    return rows


def test_the_appendix_and_the_radiology_profile_list_the_same_terms():
    table = read_toml(RADIOLOGY_OUTPUTS)["check"]["forbidden_terms"]
    machine = {
        wrong: (entry["correct"], layer)
        for layer in ("always", "context")
        for wrong, entry in table[layer].items()
    }
    assert _appendix_rows() == machine


def test_the_initial_list_is_the_one_the_user_ruled():
    terms = loader.forbidden_terms("radiology")

    assert set(terms["always"]) == {
        "蛛網膜", "外旋神經", "頜", "腭", "遠端轉移", "帶狀核",
    }
    assert {wrong: entry["cue"] for wrong, entry in terms["context"].items()} == {
        "栓塞": "thrombo",
        "遠端": "remote",
        "水腦症": "hydranencephal",
        "腦脊膜膨出": "meningoencephalocele",
        "神經節區": "ganglionic",
        "特徵性": "pathognomonic",
    }


def test_generic_ships_no_list():
    assert loader.forbidden_terms("generic") == {"always": {}, "context": {}}


def test_an_overlay_adds_one_term_without_restating_the_list(tmp_path, monkeypatch):
    overlay = tmp_path / ".lecture2notes"
    overlay.mkdir()
    (overlay / "outputs.toml").write_text(
        '[check.forbidden_terms.always]\n'
        '"錯字" = { correct = "對字", english = "word", cue = "" }\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    terms = loader.forbidden_terms("radiology")

    assert "錯字" in terms["always"]
    assert "蛛網膜" in terms["always"]


def test_a_context_entry_without_a_cue_is_dropped(tmp_path, monkeypatch):
    overlay = tmp_path / ".lecture2notes"
    overlay.mkdir()
    (overlay / "outputs.toml").write_text(
        '[check.forbidden_terms.context]\n'
        '"無線索" = { correct = "x", english = "y", cue = "" }\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert "無線索" not in loader.forbidden_terms("generic")["context"]


def test_the_profile_show_listing_names_the_terms(capsys):
    from lecture2notes.cli.main import main

    assert main(["profile", "show", "--profile", "radiology"]) == 0
    out = capsys.readouterr().out
    assert "check.forbidden_terms.always" in out
    assert "note.reader" in out


# --------------------------------------------------------------------------
# check json runs the same list over what the note will quote (B10 (c))
# --------------------------------------------------------------------------
FIXTURE = Path(__file__).parent / "fixtures" / "note_doc.json"


def _document(tmp_path: Path, profile: str = "radiology") -> Path:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["profile"] = profile
    data["takeaways_zh"][0] = "蛛網膜下腔出血要先排除動脈瘤"
    data["segments"][0]["summary_zh"] = "深部靜脈栓塞（deep vein thrombosis）的處理。"
    data["segments"][1]["bullets_zh"][0]["text"] = "肺栓塞（pulmonary embolism）"
    path = tmp_path / "talk.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "demo-0001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return path


def test_check_json_warns_on_takeaways_summaries_and_bullets(tmp_path):
    report = check.check_json(_document(tmp_path))
    hits = [f for f in report.findings if f.code == "forbidden_term"]

    assert {f.location for f in hits} == {
        "takeaways_zh[0]", "segments[1].summary_zh",
    }
    assert all(f.severity == "warn" for f in hits)


def test_check_json_is_silent_under_generic(tmp_path):
    report = check.check_json(_document(tmp_path, profile="generic"))

    assert not [f for f in report.findings if f.code == "forbidden_term"]


def test_the_json_rule_lives_in_rules_and_matches_r12():
    terms = loader.forbidden_terms("radiology")
    hits = rules.forbidden_term_matches("已有遠端轉移", "remote", terms)

    assert [(layer, wrong) for layer, wrong, _ in hits] == [("always", "遠端轉移")]
    assert check.find_term_hits is rules.forbidden_term_matches


@pytest.mark.parametrize("text, reported", [
    ("Participants (3)\nalice.chen@example.org", True),
    ("講者 王小明 ｜ 參與者面板", False),
    ("CT 影像：左側 3 cm 腫塊", False),
])
def test_check_json_warns_on_an_email_in_frame_ocr(tmp_path, text, reported):
    path = _document(tmp_path, profile="generic")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["segments"][0]["frame_ocr"] = [{"frame": "frames/demo-0001.png", "text": text}]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    hits = [f for f in check.check_json(path).findings if f.code == "frame_ocr_email"]
    assert bool(hits) is reported
    assert all(f.severity == "warn" for f in hits)
    assert all(re.match(r"segments\[1\]\.frame_ocr\[0\]", f.location) for f in hits)
