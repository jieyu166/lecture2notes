"""PotPlayer chapter files, and the switch that keeps them opt-in.

``is_enabled`` is a pure function on purpose. Writing a ``.pbf`` next to a video
changes what the player does with that video, so the decision has to be one
readable rule that ``run``, ``profile show`` and a test all consult, instead of a
condition spelled out again at each call site.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import run_cli
from lecture2notes.outputs import pbf

FIXTURES = Path(__file__).parent / "fixtures"
STEM = "placeholder-lecture"
PROFILES = Path(pbf.__file__).resolve().parents[1] / "profiles"


@pytest.fixture()
def document() -> dict:
    return json.loads((FIXTURES / "viewer_lecture_v2.json").read_text(encoding="utf-8"))


@pytest.fixture()
def lecture(tmp_path: Path, document: dict) -> Path:
    target = tmp_path / ("%s.json" % STEM)
    target.write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    return target


# -- the switch ------------------------------------------------------------
def test_a_config_without_pbf_is_off() -> None:
    assert pbf.is_enabled({}) is False
    assert pbf.is_enabled({"viewer": True, "hub": True}) is False
    assert pbf.is_enabled({"outputs": {"hub": True}}) is False


def test_no_config_at_all_is_off() -> None:
    assert pbf.is_enabled(None) is False
    assert pbf.is_enabled() is False
    assert pbf.DEFAULT_ENABLED is False


def test_an_overlay_turns_it_on() -> None:
    assert pbf.is_enabled({"pbf": True}) is True
    assert pbf.is_enabled({"outputs": {"pbf": True}}) is True
    assert pbf.is_enabled({"pbf": "true"}) is True
    assert pbf.is_enabled({"pbf": "on"}) is True


def test_an_explicit_off_stays_off() -> None:
    assert pbf.is_enabled({"pbf": False}) is False
    assert pbf.is_enabled({"pbf": "false"}) is False
    assert pbf.is_enabled({"outputs": {"pbf": False}}) is False


def test_a_malformed_switch_is_not_read_as_on() -> None:
    """The failure direction matters: a typo must not start writing files."""
    for broken in ({"pbf": "yes please"}, {"pbf": []}, {"pbf": {"enabled": True}}):
        assert pbf.is_enabled(broken) is False


def test_the_builtin_generic_profile_never_turns_it_on() -> None:
    generic = PROFILES / "generic"
    assert generic.is_dir()
    for path in generic.rglob("*.toml"):
        text = path.read_text(encoding="utf-8").replace(" ", "").lower()
        assert "pbf=true" not in text, path
    # Nothing shipped in the profile sets it, so the effective answer is off.
    assert pbf.is_enabled({}) is False


# -- the chapter file ------------------------------------------------------
def test_chapter_count_equals_segment_count(document: dict) -> None:
    assert pbf.is_enabled({"pbf": True}) is True
    lines = pbf.pbf_lines(document)
    assert lines[0] == "[Bookmark]"
    assert len(lines) - 1 == pbf.chapter_count(document) == len(document["segments"])


def test_chapters_carry_millisecond_offsets_and_titles(document: dict) -> None:
    lines = pbf.pbf_lines(document)[1:]
    assert lines[0].startswith("0=0*")
    assert lines[1].startswith("1=780000*")
    for line, segment in zip(lines, document["segments"]):
        assert segment["title"] in line


def test_an_asterisk_in_a_title_cannot_split_the_field() -> None:
    data = {"segments": [{"start_sec": 0, "title": "A*B"}]}
    line = pbf.pbf_lines(data)[1]
    assert line.count("*") == 2
    assert "＊" in line


def test_the_file_is_named_after_the_video(tmp_path: Path, lecture: Path) -> None:
    (tmp_path / ("%s 原始錄影.mp4" % STEM)).write_bytes(b"")
    stem, note = pbf.match_video_stem(lecture)
    assert stem == "%s 原始錄影" % STEM
    assert "prefix match" in note


def test_the_json_name_is_used_when_no_video_is_beside_it(lecture: Path) -> None:
    stem, note = pbf.match_video_stem(lecture)
    assert stem == STEM
    assert "no matching video" in note


# -- the command -----------------------------------------------------------
def test_cli_writes_one_chapter_per_segment(
    lecture: Path, tmp_path: Path, document: dict
) -> None:
    result = run_cli(["pbf", str(lecture)])
    assert result.returncode == 0, result.stderr
    written = tmp_path / ("%s.pbf" % STEM)
    assert written.is_file()

    lines = written.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) - 1 == len(document["segments"])
    assert not written.read_bytes().startswith(b"\xef\xbb\xbf")

    stamp = written.stat().st_mtime_ns
    again = run_cli(["pbf", str(lecture)])
    assert again.returncode == 0
    assert "[skip]" in again.stdout
    assert written.stat().st_mtime_ns == stamp


def test_cli_refuses_a_document_with_no_segments(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text('{"schema_version": "2.0", "segments": []}', encoding="utf-8")
    result = run_cli(["pbf", str(empty)])
    assert result.returncode == 2
    assert not list(tmp_path.glob("*.pbf"))
