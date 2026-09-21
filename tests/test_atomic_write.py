"""Every canonical JSON write is atomic, UTF-8 and BOM-free.

Two failures this guards against have both happened for real: a BOM that makes
the browser-side player refuse a file which looks fine in an editor, and a write
interrupted part way, leaving a truncated document that parses as far as the
reader gets before it stops.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lecture2notes.engines.corrections import write_sidecar
from lecture2notes.frames.manifest import write_manifest
from lecture2notes.frames.ocr import write_cache
from lecture2notes.schema.io import BOM, read_json, write_json_atomic

DOCUMENT = {
    "schema_version": "2.0",
    "title": "佔位講座標題",
    "segments": [{"index": 1, "summary_zh": "佔位摘要"}],
}


class Unserializable:
    """Serialising this raises after part of the document has been written."""


def temp_files(folder: Path) -> list:
    return [path.name for path in folder.iterdir() if path.name.endswith(".tmp")]


# -- the encoding contract --------------------------------------------------

def test_the_first_three_bytes_are_not_a_bom(tmp_path):
    target = write_json_atomic(tmp_path / "doc.json", DOCUMENT)
    raw = target.read_bytes()

    assert raw[:3] != BOM
    assert raw[:1] == b"{"


def test_non_ascii_is_written_unescaped_and_indented_by_two(tmp_path):
    target = write_json_atomic(tmp_path / "doc.json", DOCUMENT)
    text = target.read_text(encoding="utf-8")

    assert "佔位講座標題" in text
    assert "\\u" not in text
    assert '\n  "title": "佔位講座標題",\n' in text


def test_newlines_are_lf_only_and_the_file_ends_with_one(tmp_path):
    target = write_json_atomic(tmp_path / "doc.json", DOCUMENT)
    raw = target.read_bytes()

    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")


def test_what_was_written_reads_back_identical(tmp_path):
    target = write_json_atomic(tmp_path / "doc.json", DOCUMENT)

    assert json.loads(target.read_text(encoding="utf-8")) == DOCUMENT
    assert read_json(target) == DOCUMENT


def test_a_missing_parent_directory_is_created(tmp_path):
    target = write_json_atomic(tmp_path / "deep" / "nested" / "doc.json", DOCUMENT)

    assert target.is_file()


def test_the_temporary_file_is_written_in_the_destination_directory(tmp_path, monkeypatch):
    """A rename is only atomic within one filesystem, so the temp file is local."""
    seen = {}
    real_replace = os.replace

    def spy(source, destination):
        seen["source_parent"] = Path(source).parent
        seen["destination_parent"] = Path(destination).parent
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", spy)
    write_json_atomic(tmp_path / "doc.json", DOCUMENT)

    assert seen["source_parent"] == seen["destination_parent"] == tmp_path


# -- the atomicity contract -------------------------------------------------

def test_a_failure_part_way_through_leaves_the_original_untouched(tmp_path):
    target = tmp_path / "doc.json"
    write_json_atomic(target, DOCUMENT)
    before = target.read_bytes()

    with pytest.raises(TypeError):
        write_json_atomic(
            target, {"padding": "x" * 100_000, "boom": Unserializable()}
        )

    assert target.read_bytes() == before
    assert json.loads(target.read_text(encoding="utf-8")) == DOCUMENT
    assert temp_files(tmp_path) == []


def test_a_failure_during_the_rename_leaves_the_original_untouched(
    tmp_path, monkeypatch
):
    target = tmp_path / "doc.json"
    write_json_atomic(target, DOCUMENT)
    before = target.read_bytes()

    def explode(source, destination):
        raise OSError("interrupted")

    monkeypatch.setattr(os, "replace", explode)
    with pytest.raises(OSError):
        write_json_atomic(target, {"replaced": True})

    assert target.read_bytes() == before
    assert temp_files(tmp_path) == []


def test_a_failed_first_write_creates_no_file_at_all(tmp_path):
    target = tmp_path / "doc.json"

    with pytest.raises(TypeError):
        write_json_atomic(target, {"boom": Unserializable()})

    assert not target.exists()
    assert temp_files(tmp_path) == []


def test_nan_is_refused_rather_than_written_as_invalid_json(tmp_path):
    target = tmp_path / "doc.json"

    with pytest.raises(ValueError):
        write_json_atomic(target, {"value": float("nan")})

    assert not target.exists()


# -- everything else in the package writes through it -----------------------

def test_the_frame_manifest_is_written_without_a_bom(tmp_path):
    target = write_manifest(
        tmp_path / "demo.frames.json",
        [{"timestamp_sec": 1.0, "frame": "frames/demo-0001.png", "sha256": "ab"}],
    )

    assert target.read_bytes()[:3] != BOM
    assert read_json(target)[0]["frame"] == "frames/demo-0001.png"


def test_the_corrections_sidecar_is_written_without_a_bom(tmp_path):
    subtitle = tmp_path / "demo.srt"
    subtitle.write_text("", encoding="utf-8")

    target = write_sidecar(subtitle, {"applied": [], "table": "generic"})

    assert target.name == "demo.corrections.json"
    assert target.read_bytes()[:3] != BOM
    assert read_json(target)["table"] == "generic"


def test_the_ocr_cache_is_written_without_a_bom(tmp_path):
    target = write_cache(tmp_path / "demo.ocr_cache.json", {"frames/a.png": "佔位文字"})

    assert target.read_bytes()[:3] != BOM
    assert read_json(target) == {"frames/a.png": "佔位文字"}


def test_read_json_still_tolerates_an_incoming_bom(tmp_path):
    target = tmp_path / "legacy.json"
    target.write_bytes(BOM + b'{"stem": "demo"}')

    assert read_json(target) == {"stem": "demo"}
