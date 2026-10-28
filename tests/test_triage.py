"""`l2n triage-prompt`: keywords, vault scan and the brief. All text is placeholder."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli as _run_cli


def run_cli(args, **kwargs):
    """These tests read Chinese out of stdout; pin the child to UTF-8 so the
    cp950 CI job compares text, not mojibake (the hub tests do the same)."""
    env = {"PYTHONIOENCODING": "utf-8"}
    env.update(kwargs.pop("env", None) or {})
    return _run_cli(args, env=env, **kwargs)

from lecture2notes.notes import triage
from lecture2notes.schema.io import write_json_atomic

FIXTURES = Path(__file__).parent / "fixtures"

L3_NOTE = """---
title: Placeholder Grading Note
tags:
  - "L3"
---
# Note
The Gadget-RADS score uses MRI signal and size thresholds.
"""
PLAIN_NOTE = """---
title: Loose Note
tags: []
---
Gadget-RADS mentioned in passing with MRI.
"""


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "sub").mkdir(parents=True)
    (vault / ".obsidian").mkdir()
    (vault / "sub" / "Gadget-RADS 分級.md").write_text(L3_NOTE, encoding="utf-8")
    (vault / "Loose.md").write_text(PLAIN_NOTE, encoding="utf-8")
    (vault / ".obsidian" / "Hidden.md").write_text(L3_NOTE, encoding="utf-8")
    return vault


def document() -> dict:
    data = json.loads((FIXTURES / "lecture_v2_valid.json").read_text(encoding="utf-8"))
    data["segments"][0]["title"] = "Gadget-RADS 分級與 MRI 判讀"
    data["segments"][0]["summary_zh"] = "講者說明 Gadget-RADS 如何用 MRI signal 分級。"
    data["segments"][1]["title"] = "Unrelated widget topic"
    return data


def test_keywords_rank_title_terms_and_drop_stopwords():
    words = triage.segment_keywords(document()["segments"][0])
    assert words[0] in ("Gadget-RADS", "MRI")
    assert "the" not in [w.lower() for w in words]


def test_scan_vault_reads_l3_only_and_skips_hidden(tmp_path):
    vault = make_vault(tmp_path)
    assert [n.rel for n in triage.scan_vault(vault)] == ["sub/Gadget-RADS 分級.md"]
    assert {n.rel for n in triage.scan_vault(vault, only_l3=False)} == {
        "sub/Gadget-RADS 分級.md", "Loose.md"
    }


def test_candidates_need_a_real_match(tmp_path):
    notes = triage.scan_vault(make_vault(tmp_path))
    data = document()
    hit = triage.candidates(triage.segment_keywords(data["segments"][0]), notes)
    assert hit and hit[0].note.link == "[[Gadget-RADS 分級]]"
    assert "MRI" in hit[0].snippet
    assert triage.candidates(triage.segment_keywords(data["segments"][1]), notes) == []


def test_cli_prints_brief_with_candidates_and_rules(tmp_path):
    vault = make_vault(tmp_path)
    path = write_json_atomic(tmp_path / "sample-talk.json", document())
    result = run_cli(["triage-prompt", str(path), "--vault", str(vault)])
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "[[Gadget-RADS 分級]]" in out
    assert "傾向新知" in out
    assert "第 N 段" in out and "省下幾分鐘" in out
    assert json.loads(path.read_text(encoding="utf-8")) == document()


def test_cli_requires_an_existing_vault(tmp_path):
    path = write_json_atomic(tmp_path / "sample-talk.json", document())
    result = run_cli(["triage-prompt", str(path), "--vault", str(tmp_path / "nope")])
    assert result.returncode == 2


def test_notes_written_from_this_lecture_are_excluded(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "sub" / "Series E1 note.md").write_text(
        L3_NOTE.replace("# Note", 'source: "talk E1.mp4"\n# Note'), encoding="utf-8"
    )
    (vault / "sub" / "Series E11 note.md").write_text(
        L3_NOTE.replace("# Note", 'source: "talk E11.mp4"\n# Note'), encoding="utf-8"
    )
    data = document()
    data["source"]["video"] = "E1.mp4"
    path = write_json_atomic(tmp_path / "E1.json", data)
    result = run_cli(["triage-prompt", str(path), "--vault", str(vault)])
    assert result.returncode == 0, result.stderr
    assert "排除本講座自己產生的筆記 1 篇" in result.stdout
    assert "[[Series E1 note]]" in result.stdout.split("## 各段")[0]
    assert "[[Series E11 note]]" in result.stdout.split("## 各段")[1]


def test_english_keywords_match_whole_words_only(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "Pets.md").write_text('---\ntags:\n  - "L3"\n---\npets and radiology everywhere\n', encoding="utf-8")
    notes = triage.scan_vault(vault)
    assert triage.candidates(["ETS", "Radio"], notes) == []


def test_scope_limits_the_scan(tmp_path):
    vault = make_vault(tmp_path)
    assert triage.scan_vault(vault, scope=["nope"]) == []
    assert [n.rel for n in triage.scan_vault(vault, scope=["sub"])] == ["sub/Gadget-RADS 分級.md"]


def test_failure_writes_nothing_and_errors_go_to_stderr(tmp_path):
    path = write_json_atomic(tmp_path / "sample-talk.json", document())
    out = tmp_path / "sample-talk.triage.md"
    result = run_cli(["triage-prompt", str(path), "--vault", str(tmp_path / "nope"), "--out", str(out)])
    assert result.returncode == 2
    assert result.stdout == ""
    assert "vault not found" in result.stderr
    assert not out.exists()


def test_out_writes_the_brief_and_vault_comes_from_env(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    path = write_json_atomic(tmp_path / "sample-talk.json", document())
    out = tmp_path / "sample-talk.triage.md"
    monkeypatch.setenv("L2N_VAULT", str(vault))
    result = run_cli(["triage-prompt", str(path), "--out", str(out)])
    assert result.returncode == 0, result.stderr
    assert "[[Gadget-RADS 分級]]" in out.read_text(encoding="utf-8")


def test_hand_copied_notes_are_excluded_by_title_or_pattern(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "sub" / "20241102 TSIR 肝癌介入.md").write_text(L3_NOTE, encoding="utf-8")
    (vault / "sub" / "Series talk copy.md").write_text(
        L3_NOTE.replace("Placeholder Grading Note", "佔位講座標題長版本"), encoding="utf-8"
    )
    data = document()
    data["title"] = "佔位講座標題長版本"
    path = write_json_atomic(tmp_path / "sample-talk.json", data)
    result = run_cli(["triage-prompt", str(path), "--vault", str(vault), "--exclude", "20241102 TSIR*"])
    assert result.returncode == 0, result.stderr
    head = result.stdout.split("## 各段")[0]
    assert "排除本講座自己產生的筆記 2 篇" in head
    assert "[[20241102 TSIR 肝癌介入]]" in head and "[[Series talk copy]]" in head
