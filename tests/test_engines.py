"""Engine interface, metadata and `--list-engines`.

Nothing here loads a model, imports a runtime or touches the network: every
assertion is about the registry's own bookkeeping and about the probe, which is
built on ``find_spec`` and ``Path.exists`` precisely so it stays that cheap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lecture2notes import _deps, _out
from lecture2notes.cli.main import main as run_main
from lecture2notes.engines import registry
from lecture2notes.engines.base import Cue, DependencyStatus, Engine, EngineMeta
from lecture2notes.engines.breeze_ct2 import BreezeCT2Engine
from lecture2notes.engines.whisper_cpp import WhisperCppEngine

SHIPPED = ("breeze_ct2", "faster_whisper", "qwen3_asr", "whisper_cpp")


# -- registration ----------------------------------------------------------
def test_the_four_shipped_engines_are_registered():
    assert registry.names() == sorted(SHIPPED)


def test_every_shipped_engine_is_local():
    """The package ships no cloud engine; the gate exists only for plugins."""
    for name in SHIPPED:
        assert registry.meta_for(name).local is True, name


def test_default_engine_is_registered():
    assert registry.DEFAULT_ENGINE in registry.names()


@pytest.mark.parametrize("name", SHIPPED)
def test_metadata_carries_every_documented_field(name):
    meta = registry.meta_for(name)
    assert meta.name == name
    assert isinstance(meta.local, bool)
    assert isinstance(meta.needs_gpu, bool)
    assert isinstance(meta.native_timestamps, bool)
    assert meta.default_model, "%s must name a default model" % name


def test_native_timestamps_is_the_documented_spelling():
    """The design says `native_timestamps`; the dataclass field is `has_timestamps`."""
    meta = registry.meta_for("faster_whisper")
    assert meta.native_timestamps is meta.has_timestamps
    assert meta.to_dict()["native_timestamps"] is True


def test_gpu_flags_match_what_each_backend_actually_needs():
    assert registry.meta_for("whisper_cpp").needs_gpu is False
    assert registry.meta_for("faster_whisper").needs_gpu is False
    assert registry.meta_for("breeze_ct2").needs_gpu is True
    assert registry.meta_for("qwen3_asr").needs_gpu is True


def test_unknown_engine_names_the_known_ones():
    with pytest.raises(KeyError) as excinfo:
        registry.create("does_not_exist")
    assert "breeze_ct2" in str(excinfo.value)


def test_create_returns_an_engine_instance():
    engine = registry.create("faster_whisper", model="tiny")
    assert isinstance(engine, Engine)
    assert engine.name == "faster_whisper"
    assert engine.model == "tiny"


# -- the interface itself --------------------------------------------------
def test_engine_subclass_only_has_to_supply_check_and_transcribe():
    class Fake(Engine):
        meta = EngineMeta(
            name="fake_interface", local=True, needs_gpu=False,
            has_timestamps=True, default_model="none",
        )

        def check(self) -> None:
            return None

        def transcribe(self, audio, lang):
            return [Cue(0.0, 1.0, "hello")]

    engine = Fake()
    assert engine.probe() == DependencyStatus("fake_interface", True, "")
    cues = engine.transcribe(Path("unused.wav"), "en")
    assert [(c.start, c.end, c.text) for c in cues] == [(0.0, 1.0, "hello")]


def test_default_probe_reports_a_missing_dependency_without_raising():
    class Fake(Engine):
        meta = EngineMeta(
            name="fake_missing", local=True, needs_gpu=False,
            has_timestamps=False, default_model="none",
        )

        def check(self) -> None:
            raise _deps.MissingDependency("thing", "pip install thing")

    status = Fake().probe()
    assert status.satisfied is False
    assert "thing" in status.detail
    assert "pip install thing" in status.detail


# -- probing ---------------------------------------------------------------
def test_probe_does_not_import_the_runtime(monkeypatch):
    """A probe must answer from metadata, never by importing a model stack."""
    def explode(*args, **kwargs):
        raise AssertionError("probe imported a runtime")

    monkeypatch.setattr(_deps, "require_module", explode)
    monkeypatch.setattr(_deps, "require_qwen_asr", explode)
    for name in SHIPPED:
        registry.probe(name)


def test_probe_reports_a_missing_ct2_model_directory(tmp_path):
    engine = BreezeCT2Engine(model_dir=tmp_path / "nope")
    status = engine.probe()
    assert status.satisfied is False
    assert "ct2_model" in status.detail
    assert "l2n convert-model" in status.detail


def test_probe_accepts_a_ct2_directory_that_has_a_model_file(tmp_path, monkeypatch):
    (tmp_path / "model.bin").write_bytes(b"not really a model")
    monkeypatch.setattr(_deps, "module_available", lambda name: True)
    status = BreezeCT2Engine(model_dir=tmp_path).probe()
    assert status.satisfied is True
    assert str(tmp_path) in status.detail


def test_probe_reports_a_missing_whisper_cpp_binary_and_model(monkeypatch, tmp_path):
    monkeypatch.delenv("LECTURE2NOTES_WHISPER_CPP_BIN", raising=False)
    monkeypatch.delenv("LECTURE2NOTES_WHISPER_CPP_MODEL", raising=False)
    monkeypatch.delenv("WHISPER_SRT_BIN", raising=False)
    monkeypatch.delenv("WHISPER_SRT_MODEL", raising=False)
    status = WhisperCppEngine(binary=str(tmp_path / "no-such.exe")).probe()
    assert status.satisfied is False
    assert "ggml model" in status.detail


def test_whisper_cpp_honours_the_legacy_environment_variables(monkeypatch, tmp_path):
    """An existing rad-workflow environment keeps working after the rename."""
    binary = tmp_path / "main.exe"
    model = tmp_path / "ggml.bin"
    binary.write_bytes(b"")
    model.write_bytes(b"")
    monkeypatch.delenv("LECTURE2NOTES_WHISPER_CPP_BIN", raising=False)
    monkeypatch.delenv("LECTURE2NOTES_WHISPER_CPP_MODEL", raising=False)
    monkeypatch.setenv("WHISPER_SRT_BIN", str(binary))
    monkeypatch.setenv("WHISPER_SRT_MODEL", str(model))
    engine = WhisperCppEngine()
    assert engine.binary == str(binary)
    assert engine.model == str(model)
    assert engine.probe().satisfied is True


def test_package_name_wins_over_the_legacy_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("LECTURE2NOTES_WHISPER_CPP_BIN", "new.exe")
    monkeypatch.setenv("WHISPER_SRT_BIN", "old.exe")
    assert WhisperCppEngine().binary == "new.exe"


def test_probe_of_a_plugin_whose_constructor_refuses_options():
    class Picky(Engine):
        meta = EngineMeta(
            name="picky", local=True, needs_gpu=False,
            has_timestamps=False, default_model="none",
        )

        def __init__(self, required):  # no default: cannot be built by probe()
            super().__init__()

    registry.register(Picky)
    try:
        status = registry.probe("picky")
    finally:
        registry.unregister("picky")
    assert status.satisfied is False
    assert "cannot construct" in status.detail


# -- the listing -----------------------------------------------------------
def test_engine_lines_has_a_header_and_one_row_per_engine():
    lines = registry.engine_lines()
    assert lines[0] == registry.LIST_HEADER
    assert len(lines) == len(SHIPPED) + 1
    for name, row in zip(sorted(SHIPPED), lines[1:]):
        assert row.startswith(name)


def test_engine_lines_are_ascii_only():
    """A cp950 console must be able to print the table."""
    for row in registry.engine_lines():
        row.encode("ascii")


def test_engine_lines_state_the_metadata_flags():
    row = next(r for r in registry.engine_lines() if r.startswith("whisper_cpp"))
    fields = row.split()
    assert fields[:4] == ["whisper_cpp", "yes", "no", "yes"]


def test_engine_lines_mark_an_unsatisfied_dependency(monkeypatch):
    monkeypatch.setattr(_deps, "module_available", lambda name: False)
    row = next(r for r in registry.engine_lines() if r.startswith("qwen3_asr"))
    assert "missing:" in row
    assert "qwen_asr" in row


def test_list_engines_prints_four_rows_and_exits_zero(capsys):
    code = run_main(["transcribe", "--list-engines"])
    captured = capsys.readouterr()
    rows = [line for line in captured.out.splitlines() if line.strip()]
    assert code == 0
    assert rows[0] == registry.LIST_HEADER
    assert len(rows) == 5
    assert [row.split()[0] for row in rows[1:]] == sorted(SHIPPED)


def test_list_engines_does_not_require_lang_or_ffmpeg(monkeypatch, capsys):
    """Listing engines is a question, not a run: it needs no video stack."""
    def explode(name, *args, **kwargs):
        raise AssertionError("--list-engines required %s" % name)

    monkeypatch.setattr(_deps, "require", explode)
    assert run_main(["transcribe", "--list-engines"]) == 0
    assert "--lang is required" not in capsys.readouterr().out


def test_list_engines_still_prints_under_quiet(capsys):
    """--quiet silences progress, not the answer the user asked for."""
    assert run_main(["transcribe", "--list-engines", "--quiet"]) == 0
    assert registry.LIST_HEADER in capsys.readouterr().out


def test_transcribe_without_lang_still_exits_two():
    _out.reset()
    assert run_main(["transcribe", "video.mp4"]) == 2
