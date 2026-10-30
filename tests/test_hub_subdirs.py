"""`l2n hub --subdirs`: lectures that live one folder down.

A course folder grows by accretion: a few lectures at the top, later ones each
in their own dated folder with its own ``frames/``. The hub page still sits at
the top, so every link a subfolder lecture contributes, the card, the thumbnail
and every search hit, has to carry the subfolder, or the page is a set of dead
links that look like lectures.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List
from urllib.parse import quote, unquote

import pytest

from conftest import run_cli
from lecture2notes.outputs import hub

FIXTURES = Path(__file__).parent / "fixtures"
ROOT_STEM = "11501-01-講者甲醫師-佔位主題一"
DATED = "20250315"
#: A folder name with a space and a full-width bar, as real course folders have.
ODD_DIR = "2025 06｜加場"
ODD_STEM = "加場 講座"
UTF8 = {"PYTHONIOENCODING": "utf-8"}


def _install(folder: Path, fixture: str, stem: str, viewer: bool = True) -> None:
    folder.mkdir(parents=True, exist_ok=True)
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
    if viewer:
        (folder / ("%s.viewer.html" % stem)).write_text("<html></html>", encoding="utf-8")


@pytest.fixture()
def course(tmp_path: Path) -> Path:
    _install(tmp_path, "hub_lecture_a.json", ROOT_STEM)
    _install(tmp_path / DATED, "hub_lecture_b.json", DATED)
    _install(tmp_path / ODD_DIR, "hub_lecture_a.json", ODD_STEM)
    return tmp_path


def embedded_index(page: str) -> List[Dict]:
    match = re.search(
        r'<script id="hub-index" type="application/json">(.*?)</script>', page, re.DOTALL
    )
    assert match, "the hub page has no embedded search index"
    return json.loads(match.group(1))


def _by_key(cards) -> Dict[str, Dict]:
    return {card["key"]: card for card in cards}


# -- collecting --------------------------------------------------------------
def test_subfolder_lectures_are_left_out_without_the_flag(course: Path) -> None:
    assert [card["key"] for card in hub.collect(course)] == [ROOT_STEM]


def test_subfolder_lectures_get_cards_with_the_flag(course: Path) -> None:
    cards = _by_key(hub.collect(course, subdirs=True))

    assert set(cards) == {
        ROOT_STEM, "%s/%s" % (DATED, DATED), "%s/%s" % (ODD_DIR, ODD_STEM)
    }
    assert cards[ROOT_STEM]["dir"] == ""
    assert cards["%s/%s" % (DATED, DATED)]["dir"] == DATED


def test_only_one_level_is_scanned_and_asset_folders_are_skipped(course: Path) -> None:
    _install(course / DATED / "deeper", "hub_lecture_a.json", "too-deep")
    _install(course / "_drafts", "hub_lecture_a.json", "draft")
    # A stray lecture-shaped JSON inside frames/ is an asset, not a lecture.
    _install(course / "frames", "hub_lecture_a.json", "asset")

    keys = [card["key"] for card in hub.collect(course, subdirs=True)]

    assert not any("too-deep" in key or "draft" in key or "asset" in key for key in keys)
    assert len(keys) == 3


def test_subfolder_cards_carry_the_folder_prefix(course: Path) -> None:
    card = _by_key(hub.collect(course, subdirs=True))["%s/%s" % (DATED, DATED)]

    assert card["viewer"] == "%s/%s.viewer.html" % (DATED, DATED)
    assert card["thumb"].startswith("%s/frames/" % DATED)
    assert (course / card["viewer"]).is_file()
    assert (course / card["thumb"]).is_file()


def test_root_cards_keep_their_unprefixed_paths(course: Path) -> None:
    card = _by_key(hub.collect(course, subdirs=True))[ROOT_STEM]

    assert card["viewer"] == "%s.viewer.html" % ROOT_STEM
    assert card["thumb"].startswith("frames/")


def test_subfolder_and_root_cards_share_one_order(course: Path) -> None:
    (course / hub.TITLES_FILE).write_text(
        json.dumps({"%s/%s" % (DATED, DATED): {"no": "00"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    keys = [card["key"] for card in hub.collect(course, subdirs=True)]

    # "00" < "01" (the root lecture's number); the unnumbered one sorts last.
    assert keys == ["%s/%s" % (DATED, DATED), ROOT_STEM, "%s/%s" % (ODD_DIR, ODD_STEM)]


# -- titles ------------------------------------------------------------------
def test_titles_key_with_the_subfolder(course: Path) -> None:
    (course / hub.TITLES_FILE).write_text(
        json.dumps(
            {"%s/%s" % (DATED, DATED): {"no": "5", "speaker": "講者丙", "topic": "第五場"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    card = _by_key(hub.collect(course, subdirs=True))["%s/%s" % (DATED, DATED)]

    assert (card["no"], card["speaker"], card["topic"]) == ("5", "講者丙", "第五場")


def test_titles_key_by_bare_stem_is_the_fallback(course: Path) -> None:
    (course / hub.TITLES_FILE).write_text(
        json.dumps({DATED: {"topic": "只用 stem"}}, ensure_ascii=False), encoding="utf-8"
    )

    card = _by_key(hub.collect(course, subdirs=True))["%s/%s" % (DATED, DATED)]

    assert card["topic"] == "只用 stem"


def test_the_qualified_titles_key_wins_over_the_stem(course: Path) -> None:
    (course / hub.TITLES_FILE).write_text(
        json.dumps(
            {DATED: {"topic": "stem"}, "%s/%s" % (DATED, DATED): {"topic": "qualified"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    card = _by_key(hub.collect(course, subdirs=True))["%s/%s" % (DATED, DATED)]

    assert card["topic"] == "qualified"


def test_lecture_key_uses_a_forward_slash() -> None:
    assert hub.lecture_key("x", "") == "x"
    assert hub.lecture_key("x", "d") == "d/x"


# -- the page ----------------------------------------------------------------
def test_page_links_encode_the_subfolder(course: Path) -> None:
    result = hub.build(course, subdirs=True)
    page = result["path"].read_text(encoding="utf-8")

    encoded = quote("%s/%s.viewer.html" % (ODD_DIR, ODD_STEM))
    assert "%20" in encoded and "/" in encoded
    assert 'href="%s"' % encoded in page
    assert 'src="%s' % quote("%s/frames/" % ODD_DIR) in page
    assert 'data-stem="%s/%s"' % (ODD_DIR, ODD_STEM) in page
    assert result["missing"] == []


def test_search_hits_deep_link_into_the_subfolder(course: Path) -> None:
    result = hub.build(course, subdirs=True)
    rows = embedded_index(result["path"].read_text(encoding="utf-8"))

    viewers = {row["v"] for row in rows}
    assert "%s/%s.viewer.html" % (DATED, DATED) in viewers
    assert "%s/%s.viewer.html" % (ODD_DIR, ODD_STEM) in viewers
    # Every hit opens a viewer that is really there, relative to the page.
    for viewer in viewers:
        assert (course / viewer).is_file(), viewer


def test_link_check_covers_subfolder_viewers(course: Path) -> None:
    (course / DATED / ("%s.viewer.html" % DATED)).unlink()

    result = hub.build(course, subdirs=True)

    assert [unquote(href) for href in result["missing"]] == [
        "%s/%s.viewer.html" % (DATED, DATED)
    ]


# -- output name -------------------------------------------------------------
@pytest.mark.parametrize("name", ["index.html", "課程 首頁.html"])
def test_output_names_that_are_accepted(name: str) -> None:
    assert hub.valid_output_name(name)


@pytest.mark.parametrize("name", ["", ".", "..", "sub/index.html", "sub\\index.html", "C:index.html"])
def test_output_names_that_are_refused(name: str) -> None:
    assert not hub.valid_output_name(name)


def test_hub_path_takes_the_output_name(tmp_path: Path) -> None:
    assert hub.hub_path(tmp_path) == tmp_path / hub.HUB_FILENAME
    assert hub.hub_path(tmp_path, "index.html") == tmp_path / "index.html"


# -- the command -------------------------------------------------------------
def test_cli_writes_index_html_with_subfolder_lectures(course: Path) -> None:
    result = run_cli(
        ["hub", str(course), "--subdirs", "--output", "index.html"], env=UTF8
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 lectures" in result.stdout
    assert (course / "index.html").is_file()
    assert not (course / hub.HUB_FILENAME).exists()


def test_cli_fails_on_a_missing_subfolder_viewer(course: Path) -> None:
    (course / DATED / ("%s.viewer.html" % DATED)).unlink()

    result = run_cli(["hub", str(course), "--subdirs"], env=UTF8)

    assert result.returncode == 2
    assert "%s/%s.viewer.html" % (DATED, DATED) in result.stdout + result.stderr


def test_cli_refuses_an_output_path(course: Path) -> None:
    result = run_cli(["hub", str(course), "--output", "sub/index.html"], env=UTF8)

    assert result.returncode == 2
    assert not (course / "sub").exists()


def test_cli_preflight_lists_the_page_and_the_lectures(course: Path) -> None:
    (course / DATED / ("%s.viewer.html" % DATED)).unlink()

    def snapshot() -> Dict[str, int]:
        return {
            str(p.relative_to(course)): p.stat().st_mtime_ns
            for p in sorted(course.rglob("*"))
        }

    before = snapshot()
    result = run_cli(
        ["hub", str(course), "--subdirs", "--output", "index.html", "--preflight"],
        env=UTF8,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot() == before
    assert not (course / "index.html").exists()
    out = result.stdout
    assert "index.html (new)" in out
    assert "hub lecture %s.viewer.html (ok)" % ROOT_STEM in out
    assert "hub lecture %s/%s.viewer.html (viewer missing)" % (DATED, DATED) in out
    assert "hub lecture %s/%s.viewer.html (ok)" % (ODD_DIR, ODD_STEM) in out
