"""`--preflight` lists what a run would write and touches nothing.

The destructive part of this pipeline is not the computation, it is the
overwrite: a viewer, a hub page and a note are all regenerated in place, and a
run that silently replaces a hand-edited file is indistinguishable from one that
created it. `--preflight` exists to make that difference visible beforehand,
which only means anything if the listing itself is provably read-only.

So the proof is the same in every test here: record every file under the folder
with its size and mtime, run the preflight, record again, and require the two
recordings to be identical. Asserting "no new files appeared" would not catch a
rewrite that produced identical bytes with a new mtime, and that is exactly the
mistake a preflight could make.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conftest import tree_state

from lecture2notes import exit_codes
from lecture2notes.cli.main import main as cli_entry
from lecture2notes.outputs import hub as hub_mod
from lecture2notes.outputs import viewer as viewer_mod
from lecture2notes.outputs.plan import CREATE, OVERWRITE, plan_for

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def course(tmp_path):
    """A folder with two lectures, one of which already has its viewer."""
    for name in ("hub_lecture_a.json", "hub_lecture_b.json"):
        shutil.copy(FIXTURES / name, tmp_path / name)
    document = tmp_path / "hub_lecture_a.json"
    viewer_mod.viewer_path(document).write_text("<html>old</html>", encoding="utf-8")
    return tmp_path


def _preflight(argv, folder):
    """Run a preflight and return (exit code, printed lines, unchanged?)."""
    before = tree_state(folder)
    code = cli_entry(argv)
    return code, tree_state(folder) == before


# -- the scenario: preflight touches nothing --------------------------------
def test_hub_preflight_changes_no_file_and_no_mtime(course, capsys):
    code, unchanged = _preflight(["hub", str(course), "--preflight"], course)
    printed = capsys.readouterr().out

    assert code == exit_codes.OK
    assert unchanged
    assert printed.strip().startswith("hub ")
    assert str(hub_mod.hub_path(course)) in printed


def test_viewer_preflight_changes_no_file_and_no_mtime(course, capsys):
    document = course / "hub_lecture_a.json"

    code, unchanged = _preflight(
        ["viewer", str(document), "--preflight"], course
    )
    printed = capsys.readouterr().out

    assert code == exit_codes.OK
    assert unchanged
    assert str(viewer_mod.viewer_path(document)) in printed


def test_run_preflight_changes_no_file_and_no_mtime(synthetic_lecture, capsys):
    if synthetic_lecture.video is None:
        pytest.skip("ffmpeg is not available, so there is no recording to plan for")

    code, unchanged = _preflight(
        ["run", str(synthetic_lecture.video), "--lang", "zh", "--preflight"],
        synthetic_lecture.root,
    )
    printed = capsys.readouterr().out

    assert code == exit_codes.OK
    assert unchanged
    for target in (
        synthetic_lecture.subtitle, synthetic_lecture.manifest,
        synthetic_lecture.document, synthetic_lecture.note,
    ):
        assert str(target) in printed


def test_run_preflight_still_lists_targets_with_nothing_on_disk(tmp_path, capsys):
    """The listing is most useful before the first run, so it must work then."""
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"not a real video, only a name to plan from")
    before = tree_state(tmp_path)

    code = cli_entry(["run", str(video), "--lang", "zh", "--preflight"])
    printed = capsys.readouterr().out

    assert code == exit_codes.OK
    assert tree_state(tmp_path) == before
    assert "talk.srt (new)" in printed
    assert "talk.frames.json (new)" in printed
    assert "talk.v4.md (new)" in printed


# -- the listing says which targets already exist ---------------------------
def test_an_existing_target_is_listed_as_an_overwrite(course, capsys):
    document = course / "hub_lecture_a.json"

    cli_entry(["viewer", str(document), "--preflight"])
    printed = capsys.readouterr().out

    assert "viewer %s" % OVERWRITE in printed
    assert "(exists)" in printed


def test_an_absent_target_is_listed_as_a_create(course, capsys):
    document = course / "hub_lecture_b.json"

    cli_entry(["viewer", str(document), "--preflight"])
    printed = capsys.readouterr().out

    assert "viewer %s" % CREATE in printed
    assert "(new)" in printed


def test_plan_for_reads_the_filesystem_and_writes_nothing(tmp_path):
    present = tmp_path / "present.html"
    present.write_text("x", encoding="utf-8")
    before = tree_state(tmp_path)

    planned = plan_for([present, tmp_path / "absent.html"])

    assert [item.action for item in planned] == [OVERWRITE, CREATE]
    assert [item.exists for item in planned] == [True, False]
    assert tree_state(tmp_path) == before


# -- a preflight must not need what a real run needs ------------------------
def test_run_preflight_does_not_require_ffmpeg(tmp_path, monkeypatch, capsys):
    from lecture2notes import _deps

    def refuse(name, *args, **kwargs):
        raise AssertionError("preflight asked for the dependency %r" % name)

    monkeypatch.setattr(_deps, "require", refuse)
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"placeholder")

    assert cli_entry(
        ["run", str(video), "--lang", "zh", "--preflight"]
    ) == exit_codes.OK
    assert capsys.readouterr().out.strip()


def test_run_preflight_still_refuses_without_a_language(tmp_path, capsys):
    """The one gate that fires before the listing, because it always does."""
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"placeholder")
    before = tree_state(tmp_path)

    code = cli_entry(["run", str(video), "--preflight"])

    assert code == exit_codes.ERROR
    assert "--lang is required" in capsys.readouterr().out
    assert tree_state(tmp_path) == before


# -- the pbf target appears only when the profile switched it on ------------
def test_the_chapter_file_is_planned_only_when_the_profile_enables_it(
    tmp_path, monkeypatch, capsys
):
    from lecture2notes.outputs import pbf as pbf_mod

    video = tmp_path / "talk.mp4"
    video.write_bytes(b"placeholder")

    cli_entry(["run", str(video), "--lang", "zh", "--preflight"])
    assert "talk.pbf" not in capsys.readouterr().out

    monkeypatch.setattr(pbf_mod, "is_enabled", lambda config: True)
    cli_entry(["run", str(video), "--lang", "zh", "--preflight"])
    assert "talk.pbf" in capsys.readouterr().out
