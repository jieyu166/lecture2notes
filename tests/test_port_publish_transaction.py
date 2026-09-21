"""Smoke tests for the transactional publish ported from publish_transaction.py."""

from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

from lecture2notes.outputs.publish import (
    build_manifest,
    load_manifest,
    publish,
    publish_summary,
    recover,
    resolve_run_id,
    same_filesystem,
    sha256,
    validate_run_id,
)


def _roots(tmp_path):
    live = tmp_path / "live"
    stage = tmp_path / "stage"
    backup = tmp_path / "backup"
    for path in (live, stage, backup):
        path.mkdir()
    return live, stage, backup


def test_validate_run_id_accepts_a_sortable_timestamp():
    assert validate_run_id("20260921-131500") == "20260921-131500"
    assert validate_run_id("20260921-131500-02") == "20260921-131500-02"
    with pytest.raises(ValueError):
        validate_run_id("today")
    with pytest.raises(ValueError):
        validate_run_id("20261399-131500")


def test_resolve_run_id_picks_the_lowest_unused_suffix(tmp_path):
    now = datetime(2026, 9, 21, 13, 15, 0)
    assert resolve_run_id(tmp_path, now) == "20260921-131500"

    (tmp_path / "20260921-131500").mkdir()
    assert resolve_run_id(tmp_path, now) == "20260921-131500-01"


def test_same_filesystem_compares_the_anchor(tmp_path):
    assert same_filesystem(tmp_path / "a", tmp_path / "b") is True


def test_build_manifest_hashes_both_sides_and_writes_nothing(tmp_path):
    live, stage, backup = _roots(tmp_path)
    (live / "note.md").write_text("old", encoding="utf-8")
    (stage / "note.md").write_text("new", encoding="utf-8")
    (stage / "viewer.html").write_text("<html>", encoding="utf-8")

    manifest = build_manifest(
        "demo", live, stage, backup, ["note.md", "viewer.html"], run_id="20260921-131500"
    )

    assert [entry.relative_path for entry in manifest.entries] == ["note.md", "viewer.html"]
    assert manifest.entries[0].old_exists is True
    assert manifest.entries[0].old_sha256 == sha256(live / "note.md")
    assert manifest.entries[1].old_exists is False
    assert manifest.entries[1].old_sha256 is None
    assert manifest.state == "prepared"
    assert not list(backup.iterdir())  # nothing written yet


def test_build_manifest_rejects_an_escaping_path(tmp_path):
    live, stage, backup = _roots(tmp_path)
    with pytest.raises(ValueError, match="unsafe publication path"):
        build_manifest("demo", live, stage, backup, ["../outside.md"])


def test_build_manifest_rejects_a_missing_staged_input(tmp_path):
    live, stage, backup = _roots(tmp_path)
    with pytest.raises(FileNotFoundError):
        build_manifest("demo", live, stage, backup, ["absent.md"])


def test_publish_replaces_every_file_and_records_the_hashes(tmp_path):
    live, stage, backup = _roots(tmp_path)
    (live / "note.md").write_text("old", encoding="utf-8")
    (stage / "note.md").write_text("new", encoding="utf-8")
    (stage / "viewer.html").write_text("<html>", encoding="utf-8")

    manifest = build_manifest(
        "demo", live, stage, backup, ["note.md", "viewer.html"], run_id="20260921-131500"
    )
    result = publish(manifest)

    assert result.ok is True and result.rolled_back is False
    assert (live / "note.md").read_text(encoding="utf-8") == "new"
    assert (live / "viewer.html").read_text(encoding="utf-8") == "<html>"
    assert manifest.state == "committed"

    summary = publish_summary(manifest)
    assert [item["path"] for item in summary["files"]] == ["note.md", "viewer.html"]
    assert summary["files"][0]["sha256"] == sha256(live / "note.md")


def test_publish_rolls_the_whole_lecture_back_when_one_file_fails(tmp_path):
    live, stage, backup = _roots(tmp_path)
    for name, old in (("a.md", "old-a"), ("b.md", "old-b"), ("c.md", "old-c")):
        (live / name).write_text(old, encoding="utf-8")
        (stage / name).write_text("new-" + name, encoding="utf-8")
    before = {name: (live / name).read_text(encoding="utf-8") for name in ("a.md", "b.md", "c.md")}

    manifest = build_manifest(
        "demo", live, stage, backup, ["a.md", "b.md", "c.md"], run_id="20260921-131500"
    )

    calls = {"n": 0}

    def flaky_replace(source, destination):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("injected failure on the third file")
        return os.replace(source, destination)

    result = publish(manifest, replace_func=flaky_replace)

    assert result.ok is False
    assert result.rolled_back is True
    after = {name: (live / name).read_text(encoding="utf-8") for name in before}
    assert after == before
    assert manifest.state == "rolled_back"
    assert "injected failure" in (manifest.error or "")


def test_load_manifest_and_recover_round_trip(tmp_path):
    live, stage, backup = _roots(tmp_path)
    (stage / "note.md").write_text("new", encoding="utf-8")
    manifest = build_manifest("demo", live, stage, backup, ["note.md"], run_id="20260921-131500")
    publish(manifest)

    reloaded = load_manifest(manifest.manifest_path)
    assert reloaded.state == "committed"
    assert reloaded.entries[0].relative_path == "note.md"
    assert recover(manifest.manifest_path).ok is True
    assert json.loads(open(manifest.manifest_path, encoding="utf-8").read())["lecture_id"] == "demo"
