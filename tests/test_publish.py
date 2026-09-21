"""`l2n publish <stem> --dest <dir>`: all of it lands, or none of it does.

Publishing a lecture replaces several files at once. Doing that one file at a
time means a failure halfway leaves the destination holding a new viewer, an old
chapter file and a note from neither -- the pieces disagree, and nothing records
that they do. So the whole set is one transaction.

The scenario the specification names is the one that matters: the third of six
files fails its hash check, and afterwards the destination must contain exactly
the files it had before, with exactly the bytes it had before, the backup
directory must be gone, and the command must exit 2 naming the file it failed
on. The failure is injected at the verification step rather than by corrupting a
file on disk, because corrupting a file would be testing the filesystem.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import tree_state

from lecture2notes import exit_codes
from lecture2notes.cli.main import main as cli_entry
from lecture2notes.outputs import publish as publish_mod

pytest.importorskip("PIL", reason="Pillow paints the synthetic frames")


def _backup_root(dest: Path) -> Path:
    return dest.parent / (dest.name + publish_mod.BACKUP_SUFFIX)


def _summary(dest: Path, stem: str) -> dict:
    return json.loads(
        (dest / (stem + publish_mod.SUMMARY_SUFFIX)).read_text(encoding="utf-8")
    )


def _fail_on_nth(n: int):
    """A verifier that behaves normally except on its *n*th call."""
    calls = {"n": 0}
    real = publish_mod._verify_copy

    def verify(path, expected):
        calls["n"] += 1
        if calls["n"] == n:
            return False
        return real(path, expected)

    return verify, calls


# -- the successful publication ---------------------------------------------
def test_a_publication_copies_the_whole_set_and_records_every_hash(
    synthetic_lecture, tmp_path
):
    dest = tmp_path / "course"

    code = cli_entry([
        "publish", str(synthetic_lecture.document), "--dest", str(dest)
    ])
    summary = _summary(dest, synthetic_lecture.stem)

    assert code == exit_codes.OK
    published = {item["path"] for item in summary["files"]}
    assert "%s.json" % synthetic_lecture.stem in published
    assert "%s.srt" % synthetic_lecture.stem in published
    assert "%s.v4.md" % synthetic_lecture.stem in published
    assert "%s.frames.json" % synthetic_lecture.stem in published
    assert any(name.startswith("frames/") for name in published)

    for item in summary["files"]:
        copied = dest / item["path"]
        assert copied.is_file()
        assert publish_mod.sha256(copied) == item["sha256"]
        assert item["source"] == str(synthetic_lecture.root / Path(item["path"]))
        assert item["dest"] == str(dest / Path(item["path"]))


def test_the_backup_directory_is_null_when_nothing_was_replaced(
    synthetic_lecture, tmp_path
):
    dest = tmp_path / "course"

    cli_entry(["publish", str(synthetic_lecture.document), "--dest", str(dest)])

    assert _summary(dest, synthetic_lecture.stem)["backup_dir"] is None


def test_a_second_publication_backs_the_replaced_files_up(
    synthetic_lecture, tmp_path
):
    dest = tmp_path / "course"
    cli_entry(["publish", str(synthetic_lecture.document), "--dest", str(dest)])
    synthetic_lecture.note.write_text("expanded by hand\n", encoding="utf-8")

    cli_entry(["publish", str(synthetic_lecture.document), "--dest", str(dest)])
    summary = _summary(dest, synthetic_lecture.stem)

    assert summary["backup_dir"] is not None
    assert (dest / ("%s.v4.md" % synthetic_lecture.stem)).read_text(
        encoding="utf-8"
    ) == "expanded by hand\n"
    backup = (
        Path(summary["backup_dir"]) / "lectures" / synthetic_lecture.stem / "files"
        / ("%s.v4.md" % synthetic_lecture.stem)
    )
    assert backup.is_file()
    assert "expanded by hand" not in backup.read_text(encoding="utf-8")


def test_the_stem_may_be_given_bare(synthetic_lecture, tmp_path):
    dest = tmp_path / "course"

    code = cli_entry([
        "publish", str(synthetic_lecture.root / synthetic_lecture.stem),
        "--dest", str(dest),
    ])

    assert code == exit_codes.OK
    assert (dest / ("%s.json" % synthetic_lecture.stem)).is_file()


# -- the scenario: a mid-copy failure rolls the whole thing back ------------
def test_a_hash_failure_on_the_third_file_leaves_the_destination_untouched(
    synthetic_lecture, tmp_path, monkeypatch, capsys
):
    dest = tmp_path / "course"
    cli_entry(["publish", str(synthetic_lecture.document), "--dest", str(dest)])
    (dest / "hand-written.md").write_text("not ours\n", encoding="utf-8")
    synthetic_lecture.note.write_text("a newer note\n", encoding="utf-8")
    before = tree_state(dest)
    # The first publication left its own transaction record. Only the failed
    # run's directory may disappear; an earlier run's evidence is not this
    # command's to delete.
    runs_before = {p.name for p in _backup_root(dest).iterdir()}

    verify, calls = _fail_on_nth(3)
    monkeypatch.setattr(publish_mod, "_verify_copy", verify)

    code = cli_entry(["publish", str(synthetic_lecture.document), "--dest", str(dest)])
    printed = capsys.readouterr().out

    assert code == exit_codes.ERROR
    assert calls["n"] == 3, "verification must stop at the file that failed"
    assert tree_state(dest) == before
    assert {p.name for p in _backup_root(dest).iterdir()} == runs_before
    assert "%s.frames.json" % synthetic_lecture.stem in printed
    assert "publish failed on" in printed


def test_a_failed_publication_into_an_empty_destination_creates_nothing(
    synthetic_lecture, tmp_path, monkeypatch
):
    dest = tmp_path / "course"
    dest.mkdir()
    verify, _ = _fail_on_nth(3)
    monkeypatch.setattr(publish_mod, "_verify_copy", verify)

    code = cli_entry(["publish", str(synthetic_lecture.document), "--dest", str(dest)])

    assert code == exit_codes.ERROR
    assert list(dest.iterdir()) == []
    assert not _backup_root(dest).exists()


def test_no_temporary_file_survives_a_failure(
    synthetic_lecture, tmp_path, monkeypatch
):
    dest = tmp_path / "course"
    verify, _ = _fail_on_nth(2)
    monkeypatch.setattr(publish_mod, "_verify_copy", verify)

    cli_entry(["publish", str(synthetic_lecture.document), "--dest", str(dest)])

    assert not list(dest.rglob("*.tmp"))


def test_a_failed_rename_also_rolls_back(synthetic_lecture, tmp_path):
    """The window after verification is small, but it is not zero."""
    dest = tmp_path / "course"
    cli_entry(["publish", str(synthetic_lecture.document), "--dest", str(dest)])
    synthetic_lecture.note.write_text("a newer note\n", encoding="utf-8")
    before = tree_state(dest)

    calls = {"n": 0}

    def flaky_replace(source, destination):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("injected rename failure on the third file")
        return os.replace(source, destination)

    outcome = publish_mod.publish_lecture(
        synthetic_lecture.stem, dest, synthetic_lecture.root,
        replace_func=flaky_replace,
    )

    assert outcome.ok is False and outcome.code == exit_codes.ERROR
    assert tree_state(dest) == before


# -- the refusals, which happen before anything is copied -------------------
def test_a_destination_on_another_filesystem_is_refused(
    synthetic_lecture, tmp_path, monkeypatch
):
    dest = tmp_path / "course"
    monkeypatch.setattr(publish_mod, "same_filesystem", lambda a, b: False)

    outcome = publish_mod.publish_lecture(
        synthetic_lecture.stem, dest, synthetic_lecture.root
    )

    assert outcome.ok is False
    assert outcome.code == exit_codes.ERROR
    assert "different filesystems" in outcome.message
    assert not dest.exists(), "the refusal must happen before anything is created"


def test_publishing_into_the_source_folder_is_refused(synthetic_lecture):
    outcome = publish_mod.publish_lecture(
        synthetic_lecture.stem, synthetic_lecture.root, synthetic_lecture.root
    )

    assert outcome.ok is False and outcome.code == exit_codes.ERROR


def test_a_missing_canonical_json_is_a_usage_error(tmp_path, capsys):
    code = cli_entry([
        "publish", str(tmp_path / "absent"), "--dest", str(tmp_path / "out")
    ])

    assert code == exit_codes.ERROR
    assert "absent.json" in capsys.readouterr().out


def test_publish_without_a_destination_is_a_usage_error(synthetic_lecture, capsys):
    code = cli_entry(["publish", str(synthetic_lecture.document)])

    assert code == exit_codes.ERROR
    assert "--dest" in capsys.readouterr().out


# -- the file set ------------------------------------------------------------
def test_the_chapter_file_is_published_only_when_it_is_enabled(
    synthetic_lecture, tmp_path
):
    chapter = synthetic_lecture.root / ("%s.pbf" % synthetic_lecture.stem)
    chapter.write_text("[Bookmark]\n", encoding="utf-8")

    without = publish_mod.lecture_relative_paths(
        synthetic_lecture.stem, synthetic_lecture.root, pbf_enabled=False
    )
    with_pbf = publish_mod.lecture_relative_paths(
        synthetic_lecture.stem, synthetic_lecture.root, pbf_enabled=True
    )

    assert chapter.name not in without
    assert chapter.name in with_pbf


def test_the_canonical_json_is_published_first(synthetic_lecture):
    names = publish_mod.lecture_relative_paths(
        synthetic_lecture.stem, synthetic_lecture.root
    )

    assert names[0] == "%s.json" % synthetic_lecture.stem
    assert len(names) == len(set(names)), "no file is published twice"
