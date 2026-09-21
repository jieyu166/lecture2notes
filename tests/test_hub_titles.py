"""`_titles.json`: fixing what a filename cannot say.

Filenames in the wild carry a date and a room code, not a speaker and a topic.
The override file is how a course gets readable cards without renaming media
that other things point at. It is also why the file only replaces the keys it
mentions: a course usually needs one field fixed, not all three retyped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List

import pytest

from conftest import run_cli
from lecture2notes.outputs import hub

FIXTURES = Path(__file__).parent / "fixtures"
STEM_1 = "115-1-講者甲醫師-佔位主題一"
STEM_2 = "115-2-講者乙醫師-佔位主題二"
STEM_4 = "115-4-講者丁醫師-佔位主題四"
#: A real-world shape: a date and a room code, no speaker and no number.
UNNAMED = "20220925 MRI C1 wrist"
OVERRIDE_TOPIC = "腕關節（上）"


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
    _install(tmp_path, "hub_lecture_a.json", STEM_1)
    _install(tmp_path, "hub_lecture_b.json", STEM_2)
    _install(tmp_path, "hub_lecture_a.json", STEM_4)
    _install(tmp_path, "hub_lecture_b.json", UNNAMED)
    return tmp_path


def write_titles(folder: Path, mapping: dict) -> None:
    (folder / hub.TITLES_FILE).write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )


def stems_in_page(page: str) -> List[str]:
    return re.findall(r'<a class="card" href="[^"]*" data-stem="([^"]*)"', page)


# -- ordering --------------------------------------------------------------
def test_without_an_override_an_unnumbered_lecture_sorts_last(course: Path) -> None:
    assert [card["stem"] for card in hub.collect(course)] == [
        STEM_1, STEM_2, STEM_4, UNNAMED
    ]


def test_the_override_moves_the_card_to_its_number(course: Path) -> None:
    write_titles(course, {
        UNNAMED: {"no": "3", "speaker": "講者丙醫師", "topic": OVERRIDE_TOPIC}
    })
    cards = hub.collect(course)
    assert [card["no"] for card in cards] == ["1", "2", "3", "4"]
    assert cards[2]["stem"] == UNNAMED
    assert cards[2]["topic"] == OVERRIDE_TOPIC
    assert cards[2]["speaker"] == "講者丙醫師"


def test_the_page_shows_the_override_in_that_position(course: Path) -> None:
    write_titles(course, {
        UNNAMED: {"no": "3", "speaker": "講者丙醫師", "topic": OVERRIDE_TOPIC}
    })
    page = hub.build(course)["path"].read_text(encoding="utf-8")

    assert OVERRIDE_TOPIC in page
    assert "講者丙醫師" in page
    assert stems_in_page(page) == [STEM_1, STEM_2, UNNAMED, STEM_4]


# -- partial and malformed overrides ---------------------------------------
def test_only_the_keys_present_are_replaced(course: Path) -> None:
    write_titles(course, {STEM_1: {"topic": "改寫後的題目"}})
    card = hub.collect(course)[0]
    assert card["topic"] == "改寫後的題目"
    assert card["no"] == "1"
    assert card["speaker"] == "講者甲醫師"


def test_stems_absent_from_the_mapping_keep_derived_values(course: Path) -> None:
    write_titles(course, {UNNAMED: {"no": "3"}})
    cards = {card["stem"]: card for card in hub.collect(course)}
    assert cards[STEM_2]["topic"] == "佔位主題二"
    assert cards[STEM_2]["speaker"] == "講者乙醫師"
    assert cards[UNNAMED]["topic"] == UNNAMED


def test_a_broken_titles_file_is_ignored_not_fatal(course: Path) -> None:
    """A typo in an optional file must not take the whole course page down."""
    (course / hub.TITLES_FILE).write_text("{not json", encoding="utf-8")
    assert hub.load_titles(course) == {}
    assert len(hub.collect(course)) == 4


def test_the_titles_file_is_not_itself_a_lecture(course: Path) -> None:
    write_titles(course, {UNNAMED: {"no": "3"}})
    assert all(card["stem"] != "_titles" for card in hub.collect(course))


def test_apply_titles_without_a_mapping_uses_the_filename() -> None:
    assert hub.apply_titles(STEM_1, {}) == {
        "no": "1", "speaker": "講者甲醫師", "topic": "佔位主題一"
    }
    assert hub.apply_titles(UNNAMED, {}) == {"no": "", "speaker": "", "topic": UNNAMED}


# -- the command -----------------------------------------------------------
def test_cli_applies_the_override(course: Path) -> None:
    write_titles(course, {
        UNNAMED: {"no": "3", "speaker": "講者丙醫師", "topic": OVERRIDE_TOPIC}
    })
    result = run_cli(["hub", str(course)])
    assert result.returncode == 0, result.stderr
    page = (course / hub.HUB_FILENAME).read_text(encoding="utf-8")
    assert stems_in_page(page)[2] == UNNAMED
    assert OVERRIDE_TOPIC in page
