"""The viewer page: v2 bullet times, the three layers, and what it embeds.

The load-bearing assertion is the one about time. A bullet that carries a real
``t`` must be shown as a real time, and a bullet without one must be visibly an
estimate, because the reader cannot tell the difference any other way. Everything
else in this file exists so the page cannot lose a layer, a link or its offline
self-containment without a test going red.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import run_cli
from lecture2notes.acceptance.audit import canonical_snapshot
from lecture2notes.engines.base import Cue
from lecture2notes.outputs import viewer

FIXTURES = Path(__file__).parent / "fixtures"
STEM = "placeholder-lecture"

SRT = """1
00:00:02,000 --> 00:00:06,000
佔位逐字稿第一句

2
00:13:35,000 --> 00:13:39,000
佔位逐字稿第二句
"""


@pytest.fixture()
def lecture(tmp_path: Path) -> Path:
    """A lecture folder holding the canonical JSON and a subtitle, no media."""
    source = json.loads(
        (FIXTURES / "viewer_lecture_v2.json").read_text(encoding="utf-8")
    )
    target = tmp_path / ("%s.json" % STEM)
    target.write_text(
        json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    (tmp_path / ("%s.srt" % STEM)).write_text(SRT, encoding="utf-8", newline="\n")
    return target


def read_document(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def embedded(page: str, element_id: str):
    match = re.search(
        r'<script id="%s" type="application/json">(.*?)</script>' % element_id,
        page,
        re.DOTALL,
    )
    assert match, "missing embedded payload: %s" % element_id
    return json.loads(match.group(1))


def button_for(page: str, start: str) -> str:
    match = re.search(r'<button class="blk[^"]*" id="b-[^"]+" data-t="%s".*?</button>'
                      % re.escape(start), page, re.DOTALL)
    assert match, "no block button at t=%s" % start
    return match.group(0)


# -- embedded data ---------------------------------------------------------
def test_embedded_segments_match_the_json(lecture: Path) -> None:
    data = read_document(lecture)
    page = viewer.build(lecture, data)["path"].read_text(encoding="utf-8")
    payload = embedded(page, "viewer-data")

    assert [s["index"] for s in payload["segments"]] == [1, 2]
    for view, segment in zip(payload["segments"], data["segments"]):
        assert view["start"] == segment["start_sec"]
        assert view["end"] == segment["end_sec"]
        assert view["title"] == segment["title"]
        assert view["summary"] == segment["summary_zh"]
        assert view["frames"] == segment["frames"]


def test_embedded_snapshot_is_the_audit_snapshot(lecture: Path) -> None:
    """The audit reads the spine back out of the page and compares it."""
    data = read_document(lecture)
    page = viewer.build(lecture, data)["path"].read_text(encoding="utf-8")
    assert embedded(page, "canonical-snapshot") == canonical_snapshot(data)


# -- the honesty of the time labels ----------------------------------------
def test_timed_bullet_keeps_its_own_time_and_shows_no_tilde(lecture: Path) -> None:
    data = read_document(lecture)
    page = viewer.build(lecture, data)["path"].read_text(encoding="utf-8")

    block = button_for(page, "812.5")
    assert "佔位條列 2-A" in block
    assert "~" not in block
    assert "13:32" in block
    assert 'data-est="1"' not in block


def test_untimed_bullet_is_interpolated_and_marked(lecture: Path) -> None:
    data = read_document(lecture)
    page = viewer.build(lecture, data)["path"].read_text(encoding="utf-8")
    blocks = embedded(page, "viewer-data")["blocks"]

    estimated = [
        b for b in blocks
        if b["kind"] == "summary" and b["est"] and b["seg"] == "s1"
    ]
    assert [b["start"] for b in estimated] == [0.0, 390.0]
    marked = button_for(page, "390.0")
    assert "~" in marked
    assert 'data-est="1"' in marked


def test_quote_and_slide_layers_carry_real_times(lecture: Path) -> None:
    data = read_document(lecture)
    _segments, blocks = viewer.build_blocks(data, [])
    quotes = [b for b in blocks if b["kind"] == "quote"]
    slides = [b for b in blocks if b["kind"] == "slide"]

    assert [b["start"] for b in quotes] == [120.0, 840.0]
    assert not any(b["est"] for b in quotes)
    # frames/<stem>-1330.png is 13:30, not the segment start.
    assert [b["start"] for b in slides] == [120.0, 810.0]


def test_transcript_cues_land_in_their_own_segment(lecture: Path) -> None:
    data = read_document(lecture)
    cues = [Cue(2.0, 6.0, "佔位一"), Cue(815.0, 819.0, "佔位二")]
    _segments, blocks = viewer.build_blocks(data, cues)
    transcript = [b for b in blocks if b["kind"] == "transcript"]

    assert [b["seg"] for b in transcript] == ["s1", "s2"]
    assert not any(b["est"] for b in transcript)


def test_a_bullet_with_no_text_is_dropped(lecture: Path) -> None:
    data = read_document(lecture)
    data["segments"][0]["bullets_zh"].append({"text": "   ", "t": None})
    _segments, blocks = viewer.build_blocks(data, [])
    assert sum(1 for b in blocks if b["kind"] == "summary" and b["seg"] == "s1") == 2


# -- the page as an artefact -----------------------------------------------
def test_page_is_self_contained_and_offline(lecture: Path) -> None:
    page = viewer.build(lecture, read_document(lecture))["path"].read_text(
        encoding="utf-8"
    )
    assert "<link" not in page
    assert "http://" not in page and "https://" not in page
    assert page.count("<style>") == 1
    # No script tag pulls anything in: every one is inline or a JSON payload.
    assert not re.search(r"<script[^>]+src=", page)


def test_page_keeps_the_three_layers_and_the_controls(lecture: Path) -> None:
    page = viewer.build(lecture, read_document(lecture))["path"].read_text(
        encoding="utf-8"
    )
    for marker in (
        'id="spane"', 'id="tpane"', 'id="segnav"',         # summary / transcript / nav
        'data-kind="slide"', 'data-kind="quote"',          # frame and quote layers
        'id="q"', 'id="results"',                          # cross-layer search
        'data-mode="sum"', 'data-mode="split"', 'data-mode="tr"',  # reading modes
        'id="zin"', 'id="zout"',                           # font zoom
        'id="float"',                                      # floating player
        "URLSearchParams", "location.search",              # ?t= deep link
    ):
        assert marker in page, marker
    assert 'src="%s.mp4"' % STEM in page


def test_file_is_utf8_without_a_bom(lecture: Path) -> None:
    destination = viewer.build(lecture, read_document(lecture))["path"]
    raw = destination.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw


def test_json_payload_cannot_close_its_own_script_tag(lecture: Path) -> None:
    data = read_document(lecture)
    data["segments"][0]["title"] = "</script><script>alert(1)</script>"
    page = viewer.render(
        data, *viewer.build_blocks(data, []), "t", "v.mp4"
    )
    assert page.count("<script") == 3  # two JSON payloads plus the player script
    assert embedded(page, "viewer-data")["segments"][0]["title"] == (
        "</script><script>alert(1)</script>"
    )


# -- the command -----------------------------------------------------------
def test_cli_writes_the_page_then_skips(lecture: Path, tmp_path: Path) -> None:
    first = run_cli(["viewer", str(lecture)])
    assert first.returncode == 0, first.stderr
    destination = tmp_path / ("%s.viewer.html" % STEM)
    assert destination.is_file()

    stamp = destination.stat().st_mtime_ns
    second = run_cli(["viewer", str(lecture)])
    assert second.returncode == 0
    assert "[skip]" in second.stdout
    assert destination.stat().st_mtime_ns == stamp

    forced = run_cli(["viewer", str(lecture), "--force"])
    assert forced.returncode == 0
    assert "[ok]" in forced.stdout


def test_cli_preflight_writes_nothing(lecture: Path, tmp_path: Path) -> None:
    before = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}
    result = run_cli(["viewer", str(lecture), "--preflight"])
    after = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}

    assert result.returncode == 0
    assert after == before
    assert "%s.viewer.html" % STEM in result.stdout
    assert "create" in result.stdout


def test_cli_reports_a_document_with_no_segments(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text('{"schema_version": "2.0", "segments": []}', encoding="utf-8")
    result = run_cli(["viewer", str(empty)])
    assert result.returncode == 2
    assert "segments" in result.stdout
