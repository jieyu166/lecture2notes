"""The viewer on schema 2.1 documents: lines, translations, links, triage, frames.

Placeholder content only. The interactive behaviour (keys, tabs, ask-AI text)
is exercised in a browser outside the unit suite; these tests pin what the
page is built from.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lecture2notes.outputs import viewer

FIXTURES = Path(__file__).parent / "fixtures"
STEM = "placeholder-lecture"

SRT = (
    "1\n00:02:00,000 --> 00:02:04,000\nfirst line\n\n"
    "2\n00:13:35,000 --> 00:13:39,000\nsecond line\n"
)
ZH_SRT = (
    "1\n00:02:00,000 --> 00:02:04,000\n第一句\n\n"
    "2\n00:13:35,000 --> 00:13:39,000\n第二句\n"
)


def folder(tmp_path: Path, **changes) -> Path:
    data = json.loads((FIXTURES / "viewer_lecture_v2.json").read_text(encoding="utf-8"))
    data.update(changes)
    target = tmp_path / ("%s.json" % STEM)
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / ("%s.srt" % STEM)).write_text(SRT, encoding="utf-8")
    return target


def payload(page: str) -> dict:
    match = re.search(r'<script id="viewer-data" type="application/json">(.*?)</script>', page, re.S)
    return json.loads(match.group(1))


def build(path: Path) -> tuple:
    data = json.loads(path.read_text(encoding="utf-8"))
    result = viewer.build(path, data)
    return result, result["path"].read_text(encoding="utf-8")


def transcript(page: str) -> list:
    return [b for b in payload(page)["blocks"] if b["kind"] == "transcript"]


def test_lines_win_over_the_subtitle_and_carry_zh(tmp_path):
    path = folder(tmp_path, schema_version="2.1", lines=[
        {"t": 120.0, "end": 124.0, "text": "from lines", "zh": "來自 lines"},
        {"t": 815.0, "end": 819.0, "text": "second from lines"},
    ])
    result, page = build(path)
    blocks = transcript(page)
    assert [b["text"] for b in blocks] == ["from lines", "second from lines"]
    assert blocks[0]["zh"] == "來自 lines" and "zh" not in blocks[1]
    assert result["transcript"] == "lines" and result["translated"] == 1
    assert '<span class="zh">來自 lines</span>' in page


def test_zh_srt_with_identical_cues_supplies_translations(tmp_path):
    path = folder(tmp_path)
    (tmp_path / ("%s.zh.srt" % STEM)).write_text(ZH_SRT, encoding="utf-8")
    result, page = build(path)
    assert [b.get("zh") for b in transcript(page)] == ["第一句", "第二句"]
    assert result["transcript"] == "%s.zh.srt" % STEM


def test_zh_srt_with_different_cues_is_ignored(tmp_path):
    path = folder(tmp_path)
    (tmp_path / ("%s.zh.srt" % STEM)).write_text(ZH_SRT.replace("00:13:35,000", "00:13:36,000"), encoding="utf-8")
    _, page = build(path)
    assert not any("zh" in b for b in transcript(page))


def test_links_are_relative_and_name_the_bilingual_subtitle(tmp_path):
    path = folder(tmp_path)
    (tmp_path / ("%s.bi.srt" % STEM)).write_text(SRT, encoding="utf-8")
    _, page = build(path)
    links = payload(page)["links"]
    assert links == {
        "video": "%s.mp4" % STEM, "json": "%s.json" % STEM,
        "subtitle": "%s.srt" % STEM, "bilingual": "%s.bi.srt" % STEM,
    }
    assert "http://" not in page and "https://" not in page


def test_question_triage_and_overall_reach_the_page(tmp_path):
    data = json.loads((FIXTURES / "viewer_lecture_v2.json").read_text(encoding="utf-8"))
    data["segments"][0]["question"] = {"q": "Q1", "a": "A1", "answer_sec": 130}
    data["segments"][0]["triage"] = {"novelty": "已知", "reason": "r", "related_notes": ["[[N]]"]}
    path = folder(tmp_path, schema_version="2.1", segments=data["segments"],
                  triage_overall={"verdict": "只看新知段", "must_watch": [2], "est_minutes": 2, "reason": "x"})
    _, page = build(path)
    data = payload(page)
    assert data["segments"][0]["question"]["answer_sec"] == 130
    assert data["segments"][1]["question"] is None
    assert data["triage_overall"]["must_watch"] == [2]
    assert '<button class="segcard known"' in page
    assert '<span class="nov nov-已知">已知</span>' in page


def test_old_document_renders_without_new_fields(tmp_path):
    _, page = build(folder(tmp_path))
    data = payload(page)
    assert data["triage_overall"] is None and data["questions_zh"] == []
    assert all(s["question"] is None and s["triage"] is None for s in data["segments"])
    for marker in ('id="qpane"', 'id="xpane"', 'id="cpane"', 'id="tabs"', 'id="timeline"', 'id="subs"'):
        assert marker in page


def test_frame_manifest_time_beats_the_filename(tmp_path):
    data = json.loads((FIXTURES / "viewer_lecture_v2.json").read_text(encoding="utf-8"))
    data["segments"][1]["frame_ocr"] = [
        {"frame": "frames/placeholder-lecture-1330b.png", "text": "second capture in one second"}
    ]
    path = folder(tmp_path, segments=data["segments"])
    _, page = build(path)
    slide = [b for b in payload(page)["blocks"] if b["kind"] == "slide" and "second capture" in b["text"]]
    assert slide[0]["start"] == 810.0  # 13:30 read from the suffixed filename

    (tmp_path / ("%s.frames.json" % STEM)).write_text(json.dumps({"frames": [
        {"frame": "frames/placeholder-lecture-1330b.png", "timestamp_sec": 811.4},
    ]}), encoding="utf-8")
    _, page = build(path)
    slide = [b for b in payload(page)["blocks"] if b["kind"] == "slide" and "second capture" in b["text"]]
    assert slide[0]["start"] == 811.4
