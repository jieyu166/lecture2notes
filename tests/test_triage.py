"""`l2n triage-prompt`: keywords, vault scan and the brief. All text is placeholder."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli

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
