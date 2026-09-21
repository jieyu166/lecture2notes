"""Hub link integrity: every relative href must resolve to a real file.

A hub full of dead links is worse than no hub. It looks like the lecture is
there and it is not, and because the page builds fine nothing else in the
pipeline would ever notice. So the links are checked against the filesystem
after the page is written, and a missing target is an error with exit 2, not a
warning to scroll past.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote, unquote

import pytest

from conftest import run_cli
from lecture2notes.outputs import hub

FIXTURES = Path(__file__).parent / "fixtures"
STEM_A = "11501-01-講者甲醫師-佔位主題一"
STEM_B = "11501-02-講者乙醫師-佔位主題二"
#: The report names files whose names are Chinese. Pinning the child to UTF-8
#: matches this process's decoder; the cp950 console is exercised separately.
UTF8 = {"PYTHONIOENCODING": "utf-8"}


def _install(folder: Path, fixture: str, stem: str) -> None:
    data = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    data["stem"] = stem
    data["source"]["video"] = "%s.mp4" % stem
    (folder / ("%s.json" % stem)).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    for segment in data["segments"]:
        for frame in segment.get("frames") or []:
            path = folder / frame
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
    (folder / ("%s.viewer.html" % stem)).write_text("<html></html>", encoding="utf-8")


@pytest.fixture()
def course(tmp_path: Path) -> Path:
    _install(tmp_path, "hub_lecture_a.json", STEM_A)
    _install(tmp_path, "hub_lecture_b.json", STEM_B)
    return tmp_path


# -- the check itself ------------------------------------------------------
def test_a_complete_course_has_no_missing_links(course: Path) -> None:
    result = hub.build(course)
    assert result["hrefs"]
    assert result["missing"] == []


def test_a_deleted_viewer_is_reported(course: Path) -> None:
    (course / ("%s.viewer.html" % STEM_B)).unlink()
    result = hub.build(course)
    assert len(result["missing"]) == 1
    assert unquote(result["missing"][0]) == "%s.viewer.html" % STEM_B


def test_a_deleted_thumbnail_is_reported(course: Path) -> None:
    (course / "frames" / "hub-lecture-a-0030.png").unlink()
    result = hub.build(course)
    assert len(result["missing"]) == 1
    assert result["missing"][0].endswith(".png")


def test_links_are_percent_decoded_before_the_check(tmp_path: Path) -> None:
    """The href is encoded and the file on disk is not; comparing them raw
    would report every Chinese or spaced filename as missing."""
    stem = "2022 09 25 佔位 講座"
    _install(tmp_path, "hub_lecture_a.json", stem)
    result = hub.build(tmp_path)

    assert any("%20" in href for href in result["hrefs"])
    assert result["missing"] == []


def test_absolute_and_anchor_links_are_left_alone(tmp_path: Path) -> None:
    assert hub.missing_links(tmp_path, ["https://example.invalid/x.html"]) == []
    assert hub.missing_links(tmp_path, ["#top"]) == []


def test_a_query_or_fragment_does_not_confuse_the_check(course: Path) -> None:
    href = "%s.viewer.html?t=300#seg-s2" % STEM_A
    assert hub.missing_links(course, [quote(href, safe="?#=&")]) == []


# -- the command -----------------------------------------------------------
def test_cli_exits_2_and_names_the_missing_file(course: Path) -> None:
    (course / ("%s.viewer.html" % STEM_B)).unlink()
    result = run_cli(["hub", str(course)], env=UTF8)

    assert result.returncode == 2
    assert "%s.viewer.html" % STEM_B in result.stdout
    # The page is still written: the report is about the links, not the build.
    assert (course / hub.HUB_FILENAME).is_file()


def test_cli_exits_0_when_every_link_resolves(course: Path) -> None:
    result = run_cli(["hub", str(course)])
    assert result.returncode == 0, result.stdout
    assert "不存在" not in result.stdout


def test_cli_lists_every_missing_target_not_just_the_first(course: Path) -> None:
    (course / ("%s.viewer.html" % STEM_A)).unlink()
    (course / ("%s.viewer.html" % STEM_B)).unlink()
    result = run_cli(["hub", str(course)], env=UTF8)

    assert result.returncode == 2
    assert "%s.viewer.html" % STEM_A in result.stdout
    assert "%s.viewer.html" % STEM_B in result.stdout


def test_a_cp950_console_degrades_the_message_instead_of_crashing(
    course: Path,
) -> None:
    """A Windows console that cannot encode the filename must still get the
    exit code and a readable line, not a UnicodeEncodeError traceback."""
    (course / ("%s.viewer.html" % STEM_B)).unlink()
    result = run_cli(
        ["hub", str(course)], env={"PYTHONIOENCODING": "cp950"}, encoding="cp950"
    )

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "[error]" in result.stdout
    assert ".viewer.html" in result.stdout
