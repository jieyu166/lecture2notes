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


# -- qwen3_asr -------------------------------------------------------------
# The API these tests pin was read off the upstream source on 2026-09-21; see
# README's engine table for the links. Two facts drive most of them: the ASR
# model emits no timestamps of its own (they come from a separate forced-aligner
# checkpoint), and the aligner emits one item per character for Chinese, so cues
# have to be reassembled here rather than read off.
from lecture2notes.engines import qwen3_asr as qwen


class FakeAlignItem:
    def __init__(self, text, start_time, end_time):
        self.text = text
        self.start_time = start_time
        self.end_time = end_time


class FakeAlignResult:
    def __init__(self, items):
        self.items = items


class FakeTranscription:
    def __init__(self, text, time_stamps=None, language="Chinese"):
        self.text = text
        self.time_stamps = time_stamps
        self.language = language


class FakeQwenModel:
    """Stands in for qwen_asr.Qwen3ASRModel. Records how it was constructed."""

    created = []

    def __init__(self, kind, args, kwargs):
        self.kind = kind
        self.args = args
        self.kwargs = kwargs
        self.calls = []
        type(self).created.append(self)

    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        return cls("from_pretrained", args, kwargs)

    @classmethod
    def LLM(cls, *args, **kwargs):
        return cls("LLM", args, kwargs)

    def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        items = [
            FakeAlignItem("佔", 0.0, 0.2),
            FakeAlignItem("位", 0.2, 0.4),
            FakeAlignItem("文", 0.4, 0.6),
            FakeAlignItem("字", 0.6, 0.8),
            FakeAlignItem("。", 0.8, 0.9),
            FakeAlignItem("第", 1.2, 1.4),
            FakeAlignItem("二", 1.4, 1.6),
            FakeAlignItem("句", 1.6, 1.8),
            FakeAlignItem("。", 1.8, 1.9),
        ]
        return [FakeTranscription("佔位文字。第二句。", FakeAlignResult(items))]


@pytest.fixture()
def fake_qwen(monkeypatch):
    import types

    FakeQwenModel.created = []
    module = types.SimpleNamespace(Qwen3ASRModel=FakeQwenModel)
    monkeypatch.setattr(_deps, "require_qwen_asr", lambda: module)
    monkeypatch.setattr(
        _deps, "require_module",
        lambda name, **kw: types.SimpleNamespace(bfloat16="BF16", float16="FP16"),
    )
    return module


def test_language_codes_map_to_the_names_upstream_expects():
    assert qwen.language_name("zh") == "Chinese"
    assert qwen.language_name("en") == "English"
    assert qwen.language_name("ja") == "Japanese"
    assert qwen.language_name("auto") is None


def test_a_local_model_directory_wins_over_the_hub_name(tmp_path):
    engine = qwen.Qwen3AsrEngine(model_dir=tmp_path / "Qwen3-ASR-0.6B")
    assert engine.model_reference() == str(tmp_path / "Qwen3-ASR-0.6B")
    assert engine.model_reference() != qwen.DEFAULT_MODEL


def test_the_aligner_has_its_own_directory(tmp_path):
    engine = qwen.Qwen3AsrEngine(aligner_dir=tmp_path / "aligner")
    assert engine.aligner_reference() == str(tmp_path / "aligner")
    assert qwen.Qwen3AsrEngine().aligner_reference() == qwen.DEFAULT_ALIGNER


def test_an_unknown_backend_is_rejected_at_construction():
    with pytest.raises(ValueError) as excinfo:
        qwen.Qwen3AsrEngine(backend="onnx")
    assert "transformers" in str(excinfo.value)


def test_the_engine_declares_a_chunk_length_the_aligner_can_handle():
    """The forced aligner is documented for up to five minutes of speech."""
    assert qwen.Qwen3AsrEngine().chunk_sec == qwen.DEFAULT_CHUNK_SEC
    assert qwen.DEFAULT_CHUNK_SEC <= 300.0


def test_transformers_backend_uses_from_pretrained_with_the_aligner(fake_qwen, tmp_path):
    engine = qwen.Qwen3AsrEngine(model_dir=tmp_path / "weights")
    engine.transcribe(tmp_path / "audio.wav", "zh")
    model = FakeQwenModel.created[0]
    assert model.kind == "from_pretrained"
    assert model.args == (str(tmp_path / "weights"),)
    assert model.kwargs["forced_aligner"] == qwen.DEFAULT_ALIGNER
    assert model.kwargs["device_map"] == "cuda:0"


def test_vllm_backend_uses_the_other_constructor(fake_qwen, tmp_path):
    """vLLM is selected by calling LLM(), not by a flag on from_pretrained()."""
    engine = qwen.Qwen3AsrEngine(backend="vllm", model_dir=tmp_path / "weights")
    engine.transcribe(tmp_path / "audio.wav", "zh")
    model = FakeQwenModel.created[0]
    assert model.kind == "LLM"
    assert model.kwargs["model"] == str(tmp_path / "weights")


def test_the_model_is_loaded_once_across_several_chunks(fake_qwen, tmp_path):
    engine = qwen.Qwen3AsrEngine()
    engine.transcribe(tmp_path / "a.wav", "zh")
    engine.transcribe(tmp_path / "b.wav", "zh")
    assert len(FakeQwenModel.created) == 1


def test_transcribe_asks_for_timestamps_and_passes_the_language(fake_qwen, tmp_path):
    qwen.Qwen3AsrEngine().transcribe(tmp_path / "a.wav", "zh")
    call = FakeQwenModel.created[0].calls[0]
    assert call["return_time_stamps"] is True
    assert call["language"] == "Chinese"
    assert call["audio"] == str(tmp_path / "a.wav")


def test_transcribe_builds_cues_from_the_alignment(fake_qwen, tmp_path):
    cues = qwen.Qwen3AsrEngine().transcribe(tmp_path / "a.wav", "zh")
    assert [c.text for c in cues] == ["佔位文字。", "第二句。"]
    assert (cues[0].start, cues[0].end) == (0.0, 0.9)
    assert (cues[1].start, cues[1].end) == (1.2, 1.9)


def test_missing_timestamps_raise_a_message_naming_the_aligner(
    fake_qwen, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        FakeQwenModel, "transcribe",
        lambda self, **kw: [FakeTranscription("文字但沒有時間戳", None)],
    )
    with pytest.raises(RuntimeError) as excinfo:
        qwen.Qwen3AsrEngine().transcribe(tmp_path / "a.wav", "zh")
    assert "forced aligner" in str(excinfo.value)
    assert "--aligner-dir" in str(excinfo.value)


def test_missing_qwen_asr_raises_before_anything_is_loaded(monkeypatch, tmp_path):
    def missing():
        raise _deps.MissingDependency("qwen_asr", _deps.INSTALL_HINTS["qwen_asr"])

    monkeypatch.setattr(_deps, "require_qwen_asr", missing)
    with pytest.raises(_deps.MissingDependency):
        qwen.Qwen3AsrEngine().transcribe(tmp_path / "a.wav", "zh")


def test_missing_qwen_asr_exits_3_through_the_cli(monkeypatch, tmp_path, capsys):
    def missing():
        raise _deps.MissingDependency("qwen_asr", _deps.INSTALL_HINTS["qwen_asr"])

    monkeypatch.setattr(_deps, "require_qwen_asr", missing)
    monkeypatch.setattr(_deps, "require", lambda name, *a, **k: "fake-" + name)
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"x")
    code = run_main(["transcribe", str(video), "--lang", "zh", "--engine", "qwen3_asr"])
    assert code == 3
    assert "pip install lecture2notes[qwen]" in capsys.readouterr().out
    assert not (tmp_path / "lecture.srt").exists()


# -- cue assembly from per-character alignment -----------------------------
def align(*triples):
    return [FakeAlignItem(t, s, e) for t, s, e in triples]


def test_a_sentence_ending_closes_a_cue():
    cues = qwen.cues_from_alignment(
        align(("一", 0.0, 0.1), ("二", 0.1, 0.2), ("。", 0.2, 0.3), ("三", 0.4, 0.5))
    )
    assert [c.text for c in cues] == ["一二。", "三"]


def test_a_long_silence_closes_a_cue_even_without_punctuation():
    cues = qwen.cues_from_alignment(
        align(("一", 0.0, 0.1), ("二", 0.1, 0.2), ("三", 5.0, 5.1))
    )
    assert [c.text for c in cues] == ["一二", "三"]


def test_a_cue_never_grows_past_the_character_cap():
    long_run = [("字", i * 0.1, i * 0.1 + 0.1) for i in range(70)]
    cues = qwen.cues_from_alignment(align(*long_run))
    assert all(len(c.text) <= qwen.MAX_CUE_CHARS for c in cues)
    assert len(cues) >= 2


def test_a_cue_never_grows_past_the_duration_cap():
    slow = [("字", i * 1.0, i * 1.0 + 0.5) for i in range(20)]
    cues = qwen.cues_from_alignment(align(*slow), gap_sec=10.0)
    assert all(c.duration() <= qwen.MAX_CUE_SEC + 1.0 for c in cues)


def test_chunk_seams_never_hand_back_overlapping_cues():
    """A window's last cue can end past the next window's first cue -- a widened
    zero-length cue does exactly that -- and `check transcribe` calls the
    overlap an error, so the seam has to be trimmed after the shift.

    Real case: cue 1052 started at 4560.000 while the previous cue ended at
    4560.080, right on the 38-minute window boundary.
    """
    from lecture2notes.engines import pipeline

    stitched = pipeline.trim_seam_overlaps(
        [
            Cue(start=4559.5, end=4559.88, text="前一句"),
            Cue(start=4559.88, end=4560.08, text="啊"),
            Cue(start=4560.0, end=4563.2, text="下一塊第一句"),
        ]
    )
    assert all(b.start >= a.end for a, b in zip(stitched, stitched[1:]))
    assert [c.text for c in stitched] == ["前一句", "啊", "下一塊第一句"]


def test_a_cue_the_seam_leaves_no_room_for_is_dropped():
    """Trimming never emits a zero-length cue: with no room the cue goes."""
    from lecture2notes.engines import pipeline

    stitched = pipeline.trim_seam_overlaps(
        [
            Cue(start=100.0, end=100.4, text="前一句"),
            Cue(start=100.0, end=100.2, text="啊"),
            Cue(start=100.4, end=101.0, text="後一句"),
        ]
    )
    assert all(c.end > c.start for c in stitched)
    assert all(b.start >= a.end for a, b in zip(stitched, stitched[1:]))
    assert [c.text for c in stitched] == ["前一句", "後一句"]


def test_a_zero_width_token_still_gets_a_visible_cue():
    """The aligner gives lone interjections start == end; `check transcribe`
    rejects those cues, so the engine has to widen them (real case: cue 348
    "呃" at 1989.040 to 1989.040)."""
    cues = qwen.cues_from_alignment(
        align(("啊", 10.0, 10.0), ("好", 11.0, 11.4), ("。", 11.4, 11.5))
    )
    assert [c.text for c in cues] == ["啊", "好。"]
    assert all(c.end > c.start for c in cues)
    assert cues[0].end <= cues[1].start


def test_a_widened_cue_never_swallows_the_next_one():
    """Widening stops at the next cue's start; with no room at all the cue is
    dropped rather than emitted with a zero or negative length."""
    cues = qwen.cues_from_alignment(
        align(("啊", 10.0, 10.0), ("。", 10.0, 10.0), ("好", 10.0, 10.4))
    )
    assert all(c.end > c.start for c in cues)
    assert cues[-1].text == "好"


def test_latin_words_are_joined_with_spaces_and_cjk_is_not():
    latin = qwen.cues_from_alignment(align(("hello", 0.0, 0.5), ("world", 0.5, 1.0)))
    assert latin[0].text == "hello world"
    cjk = qwen.cues_from_alignment(align(("中", 0.0, 0.2), ("文", 0.2, 0.4)))
    assert cjk[0].text == "中文"


def test_alignment_items_accepts_a_mapping_shape():
    """A shape change upstream degrades to "no timestamps", never an exception."""
    assert qwen.alignment_items(FakeTranscription("x", None)) == []
    payload = FakeTranscription("x", {"items": [{"text": "a", "start_time": 0,
                                                 "end_time": 1}]})
    assert len(qwen.alignment_items(payload)) == 1
    assert qwen.cues_from_alignment(qwen.alignment_items(payload))[0].text == "a"


def test_items_missing_a_field_are_skipped_not_fatal():
    usable = {"text": "b", "start_time": 0, "end_time": 1}
    assert qwen.cues_from_alignment([{"text": "a"}, usable])[0].text == "b"


def test_timestamps_are_read_as_seconds_not_milliseconds():
    """Upstream annotates these int but divides by 1000 before returning them."""
    cues = qwen.cues_from_alignment(align(("字", 12.5, 13.0)))
    assert cues[0].start == 12.5 and cues[0].end == 13.0


# -- the real thing, only on a machine that has one ------------------------
QWEN_WEIGHTS_ENV = "LECTURE2NOTES_QWEN_MODEL_DIR"
QWEN_ALIGNER_ENV = "LECTURE2NOTES_QWEN_ALIGNER_DIR"


def _cuda_available() -> bool:
    try:
        import torch  # type: ignore
    except ImportError:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:  # pragma: no cover - a broken CUDA install
        return False


@pytest.mark.gpu
def test_qwen3_asr_transcribes_a_real_clip_into_a_valid_srt(tmp_path):
    """Real weights, real CUDA, real audio. Skipped unless all three are present.

    Set LECTURE2NOTES_QWEN_MODEL_DIR and LECTURE2NOTES_QWEN_ALIGNER_DIR to
    already-downloaded weight directories. The recording is the one CC BY clip
    the repository already carries, so nothing here downloads anything: an
    earlier revision pointed at ``tests/fixtures/sample.mp4``, a file
    ``test_fixture_media`` forbids anyone from adding, which made this test skip
    even on a machine that had every other piece.
    """
    import os

    from fixture_paths import FIXTURE_CLIP

    if not _deps.module_available("qwen_asr"):
        pytest.skip("qwen-asr is not installed")
    if not _cuda_available():
        pytest.skip("no CUDA device")
    model_dir = os.environ.get(QWEN_WEIGHTS_ENV)
    aligner_dir = os.environ.get(QWEN_ALIGNER_ENV)
    if not model_dir or not Path(model_dir).is_dir():
        pytest.skip("set %s to a downloaded Qwen3-ASR directory" % QWEN_WEIGHTS_ENV)
    if not aligner_dir or not Path(aligner_dir).is_dir():
        pytest.skip("set %s to a downloaded aligner directory" % QWEN_ALIGNER_ENV)
    if not FIXTURE_CLIP.is_file():
        pytest.skip("the committed fixture clip is missing")

    from lecture2notes.acceptance import check as check_mod
    from lecture2notes.engines import pipeline

    engine = qwen.Qwen3AsrEngine(model_dir=model_dir, aligner_dir=aligner_dir)
    result = pipeline.transcribe_video(
        # The clip is an English lecture excerpt; transcribing it as Chinese
        # asks the model to invent one.
        FIXTURE_CLIP,
        "en",
        engine,
        out_dir=tmp_path,
        workdir=tmp_path / "work",
    )
    assert result.srt.is_file()
    report = check_mod.Report(result.srt.name)
    check_mod.check_transcribe(result.srt, report)
    assert report.errors == []
