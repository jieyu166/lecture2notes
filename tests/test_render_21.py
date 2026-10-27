"""The skeleton note on schema 2.1: timecode links, segment questions, triage.

Placeholder content only; the base URL is an example host, not a real one.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli

from lecture2notes.acceptance.check import check_note_stage
from lecture2notes.notes import guideline, render
from lecture2notes.schema.io import write_json_atomic

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "http://media.example/course"


def document() -> dict:
    data = json.loads((FIXTURES / "lecture_v2_valid.json").read_text(encoding="utf-8"))
    data["schema_version"] = "2.1"
    data["takeaways_zh"][0] = "段落 02 佔位標題的條列 02-A 是重點"
    for segment, at in zip(data["segments"], (10, 70, 130)):
        segment["question"] = {"q": "第 %d 段題目" % segment["index"], "a": "答案 %d" % segment["index"], "answer_sec": at}
        segment["triage"] = {"novelty": "新知", "reason": "vault 沒有", "related_notes": []}
    data["segments"][0]["triage"] = {"novelty": "已知", "reason": "已有筆記涵蓋", "related_notes": ["[[Known Note]]"]}
    data["triage_overall"] = {
        "verdict": "只看新知段", "must_watch": [2, 3], "est_minutes": 2,
        "reason": "只需看第 2、3 段（共 2 分鐘，省下 1 分鐘）：第 1 段已由筆記涵蓋。",
    }
    data["questions_zh"] = [{"text": "跨段題目一", "segments": [2, 3]}]
    return data


def skeleton(data: dict, base=BASE) -> str:
    return render.render_skeleton(data, stem="sample-talk", profile="generic", media_base_url=base)


def test_times_become_links_to_the_video():
    text = skeleton(document())
    assert "([▶ 01:00](%s/sample-talk.mp4#t=60) - 02:00)" % BASE in text
    assert "- 佔位條列 02-A ([▶ 01:05](%s/sample-talk.mp4#t=65))" % BASE in text


def test_summary_point_links_to_the_segment_that_owns_it():
    text = skeleton(document())
    assert "- 段落 02 佔位標題的條列 02-A 是重點 ([▶ 01:00](%s/sample-talk.mp4#t=60))" % BASE in text
    # A placeholder takeaway shared by every segment is not anchored anywhere.
    assert "- 佔位重點第 2 條，僅供結構測試\n" in text


def test_without_a_base_url_nothing_links():
    text = skeleton(document(), base=None)
    assert "http" not in text and "▶" not in text
    assert "(00:01:00 - 00:02:00)" in text


def test_known_segment_is_one_line_with_its_note():
    text = skeleton(document())
    section = text.split("## 1、")[1].split("## 2、")[0]
    assert "已知，不重寫：見 [[Known Note]]。（已有筆記涵蓋）" in section
    assert "佔位段落摘要 01" not in section


def test_questions_come_from_segments_with_answers_and_links_then_fill_to_eight():
    text = skeleton(document())
    section = text.split("## 題目")[1].split("## 學習驗證")[0]
    lines = [line for line in section.splitlines() if line[:2].rstrip(".").isdigit()]
    assert len(lines) >= render.SEGMENT_QUESTION_MIN
    assert lines[0] == "1. 第 1 段題目" and lines[3] == "4. 跨段題目一"
    assert "> 答案 2（[▶ 講解處 01:10](%s/sample-talk.mp4#t=70)）" % BASE in section
    assert any(guideline.INFERENCE_MARK in line for line in lines)


def test_triage_section_sits_before_the_note_body():
    text = skeleton(document())
    assert text.index(render.TRIAGE_HEADING) < text.index("# Note (layer 1-3)")
    triage = text.split(render.TRIAGE_HEADING)[1].split("# Note (layer 1-3)")[0]
    assert "**只看新知段**（必看約 2 分鐘／全片 3 分鐘，省下約 1 分鐘）" in triage
    assert "第 2 段「段落 02 佔位標題」 [▶ 01:00]" in triage
    assert "| 1 段落 01 佔位標題 | [▶ 00:00](%s/sample-talk.mp4#t=0) | 已知 | 已有筆記涵蓋 | [[Known Note]] |" % BASE in triage


def test_old_document_renders_as_before():
    data = json.loads((FIXTURES / "lecture_v2_valid.json").read_text(encoding="utf-8"))
    assert render.TRIAGE_HEADING not in skeleton(data)
    assert skeleton(data, base=None) == render.render_skeleton(data, stem="sample-talk", profile="generic")


def _codes(tmp_path: Path, name: str, text: str) -> list:
    note = tmp_path / name
    note.write_text(text, encoding="utf-8")
    report = check_note_stage(tmp_path / "sample-talk.json", note, profile="generic")
    return sorted((f.code, f.location) for f in report.findings)


def test_check_note_reads_links_as_their_labels(tmp_path):
    data = document()
    write_json_atomic(tmp_path / "sample-talk.json", data)
    linked = _codes(tmp_path, "a.v4.md", skeleton(data))
    plain = _codes(tmp_path, "b.v4.md", skeleton(data, base=None))
    strip = lambda found: [(code, loc.replace("a.v4.md", "N").replace("b.v4.md", "N")) for code, loc in found]
    assert strip(linked) == strip(plain)
    assert not any(code == "R10" and loc == "segment 1" for code, loc in linked)


def test_cli_render_takes_the_base_url_from_the_environment(tmp_path):
    path = write_json_atomic(tmp_path / "sample-talk.json", document())
    result = run_cli(["render", str(path)], env={"L2N_MEDIA_BASE_URL": BASE})
    assert result.returncode == 0, result.stderr
    assert "#t=60)" in (tmp_path / "sample-talk.v4.md").read_text(encoding="utf-8")


def test_hub_card_line_states_the_verdict():
    from lecture2notes.outputs.hub import triage_line

    assert triage_line(document()) == "只看新知段（必看 2 分）"
    assert triage_line({"triage_overall": {"verdict": "跳過", "est_minutes": 0}}) == "跳過"
    assert triage_line({}) == ""


def test_refresh_replaces_only_the_fenced_blocks(tmp_path):
    data = document()
    path = write_json_atomic(tmp_path / "sample-talk.json", data)
    note = tmp_path / "sample-talk.v4.md"
    assert run_cli(["render", str(path)]).returncode == 0
    expanded = note.read_text(encoding="utf-8").replace(
        "佔位段落摘要 02，僅用於驗證結構。", "我自己擴寫的第二段內容。"
    )
    note.write_text(expanded, encoding="utf-8")

    data["takeaways_zh"][1] = "更新後的第二條重點"
    data["triage_overall"]["verdict"] = "全看"
    data["unverified_terms"] = ["新術語"]
    write_json_atomic(path, data)
    result = run_cli(["render", str(path), "--refresh"])
    assert result.returncode == 0, result.stdout + result.stderr
    text = note.read_text(encoding="utf-8")
    assert "我自己擴寫的第二段內容。" in text
    assert "更新後的第二條重點" in text and "**全看**" in text and "新術語" in text
    assert (tmp_path / "sample-talk.v4.md.bak").read_text(encoding="utf-8") == expanded
    assert "already current" in run_cli(["render", str(path), "--refresh"]).stdout


def test_refresh_on_an_unfenced_note_updates_references_and_adds_triage():
    data = document()
    fresh = render.render_skeleton(data, stem="sample-talk", profile="generic")
    old = fresh
    for name in render.MANAGED_BLOCKS:
        old = old.replace(render.MANAGED_BEGIN % name + "\n", "").replace("\n" + render.MANAGED_END % name, "")
    old = old.split(render.TRIAGE_HEADING)[0] + "# Note (layer 1-3)" + old.split("# Note (layer 1-3)", 1)[1]
    old = old.replace("- （無）", "- 舊的內容")
    data["unverified_terms"] = ["新術語"]
    text, replaced, skipped = render.refresh_note(old, render.render_skeleton(data, stem="sample-talk", profile="generic"))
    assert replaced == ["triage", "references"] and skipped == ["summary"]
    assert "新術語" in text and "舊的內容" not in text
    assert text.index(render.TRIAGE_HEADING) < text.index("# Note (layer 1-3)")


def test_links_add_the_media_offset_but_labels_keep_document_time():
    text = render.render_skeleton(document(), stem="sample-talk", profile="generic",
                                  media_base_url=BASE, media_offset=3.8)
    assert "([▶ 01:00](%s/sample-talk.mp4#t=63) - 02:00)" % BASE in text


EARLY_NOTE = """---
title: 早期筆記
noteVer: v4
---
Topics :: [[Topic]] <br>

---
# Evergreen Note
「**我自己寫的核心觀念**」

# Summary
- **我自己寫的重點一**
- 易混淆觀念：我自己寫的

# Note (layer 1-3)
## 1、段落 01 佔位標題
我自己擴寫的第一段。

## 2、段落 02 佔位標題
我自己擴寫的第二段。

## 3、段落 03 佔位標題
我自己擴寫的第三段。

### 參考來源
[^1]: 我自己的來源

## 題目
Q: 我自己的題目
A: 我自己的答案

## 閱片
"""


def _check_codes(tmp_path: Path, json_path: Path, note: Path) -> set:
    report = check_note_stage(json_path, note, profile="generic")
    return {f.code for f in report.findings if f.severity == "error"}


def test_upgrade_adds_missing_sections_and_keeps_every_line(tmp_path):
    data = json.loads((FIXTURES / "lecture_v2_valid.json").read_text(encoding="utf-8"))
    path = write_json_atomic(tmp_path / "sample-talk.json", data)
    note = tmp_path / "sample-talk.v4.md"
    note.write_text(EARLY_NOTE, encoding="utf-8")
    assert {"R1", "R4"} <= _check_codes(tmp_path, path, note)

    result = run_cli(["render", str(path), "--upgrade"])
    assert result.returncode == 0, result.stdout + result.stderr
    text = note.read_text(encoding="utf-8")
    remaining = iter(text.splitlines())
    assert all(any(line == other for other in remaining) for line in EARLY_NOTE.splitlines()), \
        "an existing line moved or vanished"
    for heading in ("## 講者骨架", "### References", "## 學習驗證"):
        assert heading in text
    assert text.index("## 講者骨架") < text.index("# Note (layer 1-3)")
    assert text.index("### References") < text.index("## 題目") < text.index("## 學習驗證")
    assert render.read_style_marker(text) == "concise"
    assert not {"R1", "R4", "R6"} & _check_codes(tmp_path, path, note)
    assert (tmp_path / "sample-talk.v4.md.bak").read_text(encoding="utf-8") == EARLY_NOTE
    assert "already has every section" in run_cli(["render", str(path), "--upgrade"]).stdout


def test_upgrade_puts_the_correction_table_above_existing_references():
    data = json.loads((FIXTURES / "lecture_v2_valid.json").read_text(encoding="utf-8"))
    early = EARLY_NOTE.replace("### 參考來源", "### References")
    fresh = render.render_skeleton(data, stem="sample-talk", profile="generic")
    text, added, _ = render.upgrade_note(early, fresh, "concise")
    assert "References correction table" in added
    section = text.split("### References")[1].split("## 題目")[0]
    assert section.index("| heard | correct | source |") < section.index("[^1]: 我自己的來源")
