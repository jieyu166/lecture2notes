"""`l2n bilingual`: export batches, merge translations, never move a cue.

All subtitle text is placeholder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import run_cli

from lecture2notes.engines import bilingual
from lecture2notes.engines.base import parse_srt
from lecture2notes.schema.io import read_json, write_json_atomic

FIXTURES = Path(__file__).parent / "fixtures"

SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nThe Lung rats category\n\n"
    "2\n00:00:03,000 --> 00:00:05,000\nThe Lung rats category\n\n"
    "3\n00:01:10,000 --> 00:01:12,500\nshows Emphysima here\n\n"
    "4\n00:01:12,500 --> 00:01:15,000\nand a nodule\n\n"
    "5\n00:02:10,000 --> 00:02:58,000\nfollow up in twelve months\n"
)
ZH = {1: "Lung-RADS 分類", 3: "這裡顯示 emphysema", 4: "還有一顆結節", 5: "十二個月後追蹤"}


def make(tmp_path: Path, with_json: bool = True) -> Path:
    srt = tmp_path / "talk.srt"
    srt.write_text(SRT, encoding="utf-8")
    (tmp_path / "talk.txt").write_text("Lung-RADS emphysema nodule follow-up Fleischner", encoding="utf-8")
    if with_json:
        data = json.loads((FIXTURES / "lecture_v2_valid.json").read_text(encoding="utf-8"))
        data["schema_version"] = "2.1"
        data["stem"] = "talk"
        data["source"]["subtitle"]["path"] = "talk.srt"
        data["source"]["subtitle"]["lang"] = "en"
        for segment in data["segments"]:
            for frame in set(segment["frames"] + [segment["frame"]]):
                (tmp_path / frame).parent.mkdir(parents=True, exist_ok=True)
                (tmp_path / frame).write_bytes(b"x")
        write_json_atomic(tmp_path / "talk.json", data)
    return srt


def target(tmp_path: Path) -> bilingual.Target:
    return bilingual.resolve(tmp_path / "talk.srt")


def export(tmp_path: Path, size: int = 2) -> dict:
    return bilingual.export(
        target(tmp_path), "en", batch_size=size, handout=tmp_path / "talk.txt",
        table={"radiology": {"Lung rats": "Lung-RADS"}, "context_sensitive": {"nodal": "nodule"}},
    )


def answer(tmp_path: Path, number: int, mapping: dict, **extra) -> None:
    payload = {"zh": {str(k): v for k, v in mapping.items()}}
    payload.update(extra)
    (target(tmp_path).work / ("batch-%03d.zh.json" % number)).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def translate_all(tmp_path: Path) -> None:
    answer(tmp_path, 1, {1: ZH[1]}, corrections=[{"heard": "Emphysima", "correct": "emphysema", "cue": 3}])
    answer(tmp_path, 2, {3: ZH[3], 4: ZH[4]}, unverified=["twelve（可能為 12）"])
    answer(tmp_path, 3, {5: ZH[5]})


def test_export_writes_batches_with_repeats_and_context(tmp_path):
    make(tmp_path)
    result = export(tmp_path)
    work = target(tmp_path).work
    assert result["batches"] == 3 and result["cues"] == 5
    first = read_json(work / "batch-001.json")
    assert first["cues"] == [
        {"id": 1, "text": "The Lung rats category"}, {"id": 2, "same_as": 1}
    ]
    second = read_json(work / "batch-002.json")
    assert [c["id"] for c in second["context_before"]] == [1, 2]
    manifest = read_json(work / "manifest.json")
    assert manifest["lang"] == "en" and manifest["cues"] == 5
    brief = (work / "brief.md").read_text(encoding="utf-8")
    assert "Lung-RADS" in brief and "原文字幕不改" in brief


def test_glossary_finds_handout_terms_near_misses_and_table_pairs(tmp_path):
    make(tmp_path)
    cues = parse_srt(tmp_path / "talk.srt")
    terms = bilingual.glossary(
        cues, "Lung-RADS emphysema nodule",
        {"radiology": {"Lung rats": "Lung-RADS"}, "context_sensitive": {"nodal": "nodule"}},
    )
    assert "nodule" in terms["keep"]
    assert "Emphysima -> emphysema" in terms["near"]
    assert terms["applied"] == ["Lung rats -> Lung-RADS"]
    assert terms["judged"] == ["nodal -> nodule"]


def test_export_refuses_chinese_and_outputs(tmp_path):
    make(tmp_path)
    with pytest.raises(bilingual.BilingualError):
        bilingual.export(target(tmp_path), "zh")
    (tmp_path / "talk.bi.srt").write_text(SRT, encoding="utf-8")
    with pytest.raises(bilingual.BilingualError):
        bilingual.resolve(tmp_path / "talk.bi.srt")


def test_merge_keeps_every_cue_and_timecode(tmp_path):
    make(tmp_path)
    export(tmp_path)
    translate_all(tmp_path)
    result = bilingual.merge(target(tmp_path))

    source = parse_srt(tmp_path / "talk.srt")
    names = sorted(p.name for p in result.outputs)
    assert names == ["talk.bi.srt", "talk.en.srt", "talk.zh.srt"]
    for path in result.outputs:
        written = parse_srt(path)
        assert [(c.start, c.end) for c in written] == [(c.start, c.end) for c in source]
    zh = [c.text for c in parse_srt(tmp_path / "talk.zh.srt")]
    assert zh == [ZH[1], ZH[1], ZH[3], ZH[4], ZH[5]]
    bi = (tmp_path / "talk.bi.srt").read_text(encoding="utf-8")
    assert "這裡顯示 emphysema\nshows Emphysima here" in bi

    data = read_json(tmp_path / "talk.json")
    assert data["lines"][0] == {"t": 1.0, "end": 5.0, "text": "The Lung rats category", "zh": ZH[1]}
    assert [line["zh"] for line in data["lines"]] == [ZH[1], ZH[3], ZH[4], ZH[5]]
    assert {"heard": "Emphysima", "correct": "emphysema", "source": "bilingual"} in data["corrections"]
    assert "twelve（可能為 12）" in data["unverified_terms"]
    assert (tmp_path / "talk.srt").read_text(encoding="utf-8") == SRT


def test_merge_reports_missing_batch_and_writes_nothing(tmp_path):
    make(tmp_path)
    export(tmp_path)
    answer(tmp_path, 1, {1: ZH[1]})
    with pytest.raises(bilingual.BilingualError) as info:
        bilingual.merge(target(tmp_path))
    assert "batch-002" in info.value.problems[0]
    assert not (tmp_path / "talk.zh.srt").exists()


@pytest.mark.parametrize("mapping, needle", [
    ({3: ZH[3]}, "no translation for cue 4"),
    ({3: ZH[3], 4: ZH[4], 5: "x"}, "does not belong"),
    ({3: ZH[3], 4: "  "}, "empty or malformed"),
    ({3: ZH[3], 4: "a --> b"}, "empty or malformed"),
])
def test_merge_rejects_bad_batches(tmp_path, mapping, needle):
    make(tmp_path)
    export(tmp_path)
    translate_all(tmp_path)
    answer(tmp_path, 2, mapping)
    with pytest.raises(bilingual.BilingualError) as info:
        bilingual.merge(target(tmp_path))
    assert any(needle in problem for problem in info.value.problems), info.value.problems


def test_changed_source_blocks_merge_and_force_keeps_old_work(tmp_path):
    srt = make(tmp_path)
    export(tmp_path)
    translate_all(tmp_path)
    srt.write_text(SRT.replace("and a nodule", "and two nodules"), encoding="utf-8")
    with pytest.raises(bilingual.BilingualError) as info:
        bilingual.merge(target(tmp_path))
    assert "changed since export" in info.value.problems[0]
    with pytest.raises(bilingual.BilingualError):
        export(tmp_path)
    bilingual.export(target(tmp_path), "en", batch_size=2, force=True)
    work = target(tmp_path).work
    assert len(list(work.glob("*.zh.stale.json"))) == 3
    assert not list(work.glob("*.zh.json"))


def test_cli_export_status_merge(tmp_path):
    make(tmp_path)
    json_path = tmp_path / "talk.json"
    result = run_cli(["bilingual", "export", str(json_path), "--batch-size", "2",
                      "--handout", str(tmp_path / "talk.txt")])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 batches" in result.stdout
    assert "1/3" not in run_cli(["bilingual", "status", str(json_path)]).stdout
    translate_all(tmp_path)
    assert "3/3" in run_cli(["bilingual", "status", str(json_path)]).stdout
    merged = run_cli(["bilingual", "merge", str(json_path)])
    assert merged.returncode == 0, merged.stdout + merged.stderr
    assert "timecodes unchanged" in merged.stdout
    assert "Emphysima" in merged.stdout + merged.stderr
    check = run_cli(["check", "json", str(json_path)])
    assert check.returncode == 0, check.stdout


def test_cli_export_needs_lang_without_document(tmp_path):
    make(tmp_path, with_json=False)
    result = run_cli(["bilingual", "export", str(tmp_path / "talk.srt")])
    assert result.returncode == 2
    assert "--lang" in result.stdout + result.stderr
    assert run_cli(["bilingual", "export", str(tmp_path / "talk.srt"), "--lang", "en"]).returncode == 0
