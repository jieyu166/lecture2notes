"""The course hub: cards, ordering, and the cross-lecture search index.

The index is the part that earns the page. A term that appeared only on a slide
is exactly the term nobody can find by any other means, so the OCR layer is
indexed alongside titles, takeaways, bullets and quotes, and every hit carries
the viewer and the second it belongs to.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

import pytest

from conftest import run_cli
from lecture2notes.outputs import hub

FIXTURES = Path(__file__).parent / "fixtures"
STEM_A = "11501-01-講者甲醫師-佔位主題一"
STEM_B = "11501-02-講者乙醫師-佔位主題二"
#: The one word that exists only in lecture B's slide OCR.
ONLY_ON_A_SLIDE = "Haglund"


def _install(folder: Path, fixture: str, stem: str, viewer: bool = True) -> Path:
    """Copy a synthetic lecture into a course folder, frames and viewer included."""
    data = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    data["stem"] = stem
    data["source"]["video"] = "%s.mp4" % stem
    target = folder / ("%s.json" % stem)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    for segment in data["segments"]:
        for frame in segment.get("frames") or []:
            path = folder / frame
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
    if viewer:
        (folder / ("%s.viewer.html" % stem)).write_text("<html></html>", encoding="utf-8")
    return target


@pytest.fixture()
def course(tmp_path: Path) -> Path:
    _install(tmp_path, "hub_lecture_a.json", STEM_A)
    _install(tmp_path, "hub_lecture_b.json", STEM_B)
    return tmp_path


def embedded_index(page: str) -> List[Dict]:
    match = re.search(
        r'<script id="hub-index" type="application/json">(.*?)</script>', page, re.DOTALL
    )
    assert match, "the hub page has no embedded search index"
    return json.loads(match.group(1))


# -- cards -----------------------------------------------------------------
def test_cards_are_built_from_the_folder(course: Path) -> None:
    cards = hub.collect(course)
    assert [card["stem"] for card in cards] == [STEM_A, STEM_B]
    assert [card["no"] for card in cards] == ["01", "02"]
    assert [card["speaker"] for card in cards] == ["講者甲醫師", "講者乙醫師"]
    assert [card["topic"] for card in cards] == ["佔位主題一", "佔位主題二"]
    assert [card["minutes"] for card in cards] == [10, 15]
    assert [len(card["frames"]) for card in cards] == [2, 2]


def test_cards_sort_by_number_then_stem_as_strings() -> None:
    cards = [
        {"no": "10", "stem": "b"}, {"no": "2", "stem": "a"},
        {"no": "", "stem": "z"}, {"no": "2", "stem": "b"},
    ]
    assert [hub.card_sort_key(card) for card in sorted(cards, key=hub.card_sort_key)] == [
        ("10", "b"), ("2", "a"), ("2", "b"), (hub.UNNUMBERED_SORT_KEY, "z"),
    ]


def test_derived_json_and_underscore_files_are_not_lectures() -> None:
    assert hub.is_lecture_json(Path("a.json"))
    assert not hub.is_lecture_json(Path("_titles.json"))
    assert not hub.is_lecture_json(Path("a.frames.json"))
    assert not hub.is_lecture_json(Path("a.frames_ocr.json"))


def test_a_lecture_without_a_viewer_still_gets_a_card(tmp_path: Path) -> None:
    """Otherwise a half-built course silently shrinks instead of reporting."""
    _install(tmp_path, "hub_lecture_a.json", STEM_A, viewer=False)
    assert hub.collect(tmp_path, require_viewer=True) == []
    assert len(hub.collect(tmp_path, require_viewer=False)) == 1


# -- the index -------------------------------------------------------------
def test_slide_only_text_is_searchable_and_points_at_its_segment(course: Path) -> None:
    page = hub.build(course)["path"].read_text(encoding="utf-8")
    index = embedded_index(page)

    hits = [row for row in index if ONLY_ON_A_SLIDE in row["x"]]
    assert len(hits) == 1
    hit = hits[0]
    assert hit["k"] == "slide"
    assert hit["v"] == "%s.viewer.html" % STEM_B
    # frames/<stem>-0745.png is 07:45, inside segment 2 (420-900 s).
    assert hit["t"] == 465

    # The word really is nowhere else: not in a title, bullet, quote or takeaway.
    others = [row for row in index if row["k"] != "slide" and ONLY_ON_A_SLIDE in row["x"]]
    assert others == []


def test_the_index_covers_every_layer(course: Path) -> None:
    index = hub.build_index(hub.collect(course))
    kinds = {row["k"] for row in index}
    assert kinds == {"takeaway", "segment", "bullet", "quote", "slide"}

    segments = [row for row in index if row["k"] == "segment"]
    assert [row["t"] for row in segments] == [0, 300, 0, 420]
    quotes = [row for row in index if row["k"] == "quote"]
    assert [row["t"] for row in quotes] == [45, 90]


def test_empty_index_text_is_dropped(course: Path) -> None:
    data = json.loads((course / ("%s.json" % STEM_A)).read_text(encoding="utf-8"))
    data["segments"][0]["bullets_zh"].append({"text": "  ", "t": None})
    data["segments"][0]["frame_ocr"].append({"frame": "frames/x-0001.png", "text": ""})
    (course / ("%s.json" % STEM_A)).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    index = hub.build_index(hub.collect(course))
    assert all(row["x"].strip() for row in index)


# -- the page --------------------------------------------------------------
def test_page_links_to_each_viewer_and_stays_offline(course: Path) -> None:
    page = hub.build(course)["path"].read_text(encoding="utf-8")
    for stem in (STEM_A, STEM_B):
        assert 'data-stem="%s"' % stem in page
    assert "http://" not in page and "https://" not in page
    assert not re.search(r"<script[^>]+src=", page)
    assert "<link" not in page
    # A search result opens the viewer at the matching second.
    assert "?t=" in page


def test_page_is_written_as_the_course_home(course: Path) -> None:
    result = hub.build(course, title="佔位課程")
    assert result["path"].name == hub.HUB_FILENAME
    assert result["path"].name == "課程首頁.html"
    raw = result["path"].read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    assert "佔位課程" in result["path"].read_text(encoding="utf-8")


# -- the command -----------------------------------------------------------
def test_cli_builds_the_hub(course: Path) -> None:
    result = run_cli(["hub", str(course)])
    assert result.returncode == 0, result.stderr
    assert (course / hub.HUB_FILENAME).is_file()
    assert "2 lectures" in result.stdout


def test_cli_preflight_writes_nothing(course: Path) -> None:
    def snapshot() -> Dict[str, int]:
        return {
            str(p.relative_to(course)): p.stat().st_mtime_ns
            for p in sorted(course.rglob("*"))
        }

    before = snapshot()
    result = run_cli(["hub", str(course), "--preflight"])
    assert result.returncode == 0
    assert snapshot() == before
    assert hub.HUB_FILENAME in result.stdout
    assert not (course / hub.HUB_FILENAME).exists()


def test_cli_reports_a_folder_with_no_lectures(tmp_path: Path) -> None:
    result = run_cli(["hub", str(tmp_path)])
    assert result.returncode == 2
    assert "hub" in result.stdout


def test_cli_refuses_a_path_that_is_not_a_folder(course: Path) -> None:
    result = run_cli(["hub", str(course / ("%s.json" % STEM_A))])
    assert result.returncode == 2
