"""Correction tables: the raw transcript survives and every replacement is logged.

The point of these tests is not that find-and-replace works. It is that the
evidence of what the engine actually heard is still on disk afterwards, and that
the correction pass cannot quietly damage the cue structure while fixing words.

`tests/fixtures/corrections_sample.srt` is synthetic placeholder text written for
this test: ten cues, twelve occurrences of the misheard term across seven of
them, so a per-occurrence count cannot be confused with a per-cue count.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from lecture2notes.engines import audio as audio_mod
from lecture2notes.engines import corrections as corrections_mod
from lecture2notes.engines import pipeline
from lecture2notes.engines.base import Cue, Engine, EngineMeta, parse_srt
from lecture2notes.engines.corrections import (
    apply_corrections,
    build_sidecar,
    correct_file,
    load_table,
)

FIXTURE = Path(__file__).parent / "fixtures" / "corrections_sample.srt"
HEARD = "口拍的"
CORRECT = "Copilot"
EXPECTED_HITS = 12

TABLE = {
    "deterministic": {HEARD: CORRECT},
    "context_sensitive": {"賦值錯誤": "指派錯誤"},
}

TIMECODE = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$")


def structure(text: str):
    """Everything about an SRT except the subtitle text: what must never change."""
    numbers, timecodes, blanks = [], [], 0
    for line in text.replace("\r", "").split("\n"):
        stripped = line.strip()
        if TIMECODE.match(stripped):
            timecodes.append(stripped)
        elif stripped.isdigit():
            numbers.append(int(stripped))
        elif not stripped:
            blanks += 1
    return numbers, timecodes, blanks


@pytest.fixture()
def sample(tmp_path):
    target = tmp_path / "lecture.srt"
    shutil.copy2(FIXTURE, target)
    return target


def test_the_fixture_really_contains_twelve_occurrences():
    """If this drifts, every count assertion below is meaningless."""
    assert FIXTURE.read_text(encoding="utf-8").count(HEARD) == EXPECTED_HITS


# -- the pure pass ---------------------------------------------------------
def test_apply_corrections_counts_every_occurrence_not_every_cue():
    text = FIXTURE.read_text(encoding="utf-8")
    fixed, hits = apply_corrections(text, load_table(TABLE))
    assert hits == [
        {"heard": HEARD, "correct": CORRECT, "count": EXPECTED_HITS,
         "source": "deterministic"}
    ]
    assert HEARD not in fixed
    assert fixed.count(CORRECT) == EXPECTED_HITS


def test_context_sensitive_entries_are_never_applied_automatically():
    """A judgement call must not be made by a string replacement."""
    text = FIXTURE.read_text(encoding="utf-8")
    fixed, hits = apply_corrections(text, load_table(TABLE))
    assert "賦值錯誤" in fixed
    assert all(hit["heard"] != "賦值錯誤" for hit in hits)


def test_longer_keys_are_applied_first():
    """Otherwise a short key eats part of a longer one before it can match."""
    table = {"deterministic": {"口拍": "X", "口拍的": "Copilot"}}
    fixed, hits = apply_corrections("口拍的", load_table(table))
    assert fixed == "Copilot"
    assert hits[0]["heard"] == "口拍的"


# -- the file pass ---------------------------------------------------------
def test_corrected_file_keeps_numbering_timecodes_and_blank_lines(sample):
    before = sample.read_text(encoding="utf-8")
    correct_file(sample, load_table(TABLE), s2t=False)
    after = sample.read_text(encoding="utf-8")
    assert structure(after) == structure(before)


def test_raw_backup_keeps_the_original_text(sample):
    correct_file(sample, load_table(TABLE), s2t=False)
    raw = sample.with_name("lecture.raw.srt")
    assert raw.is_file()
    assert raw.read_text(encoding="utf-8").count(HEARD) == EXPECTED_HITS
    assert HEARD not in sample.read_text(encoding="utf-8")


def test_raw_and_corrected_have_identical_cue_counts_and_timecodes(sample):
    correct_file(sample, load_table(TABLE), s2t=False)
    raw_cues = parse_srt(sample.with_name("lecture.raw.srt"))
    fixed_cues = parse_srt(sample)
    assert len(raw_cues) == len(fixed_cues) == 10
    assert [(c.start, c.end) for c in raw_cues] == [(c.start, c.end) for c in fixed_cues]
    assert [c.text for c in raw_cues] != [c.text for c in fixed_cues]


def test_sidecar_lists_the_replacement_with_heard_correct_count_source(sample):
    correct_file(sample, load_table(TABLE), s2t=False, table_path="profiles/generic")
    sidecar = json.loads(
        sample.with_name("lecture.corrections.json").read_text(encoding="utf-8")
    )
    assert sidecar["corrections"] == [
        {"heard": HEARD, "correct": CORRECT, "count": EXPECTED_HITS,
         "source": "deterministic"}
    ]
    assert sidecar["total_hits"] == EXPECTED_HITS
    assert sidecar["total_kinds"] == 1
    assert sidecar["backup"] == "lecture.raw.srt"
    assert sidecar["corrections_table"] == "profiles/generic"


def test_sidecar_is_written_without_a_bom_and_with_lf_endings(sample):
    correct_file(sample, load_table(TABLE), s2t=False)
    raw_bytes = sample.with_name("lecture.corrections.json").read_bytes()
    assert not raw_bytes.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw_bytes


def test_a_table_that_matches_nothing_still_leaves_a_sidecar(sample):
    """"The table ran and matched nothing" must not look like "no table ran"."""
    result = correct_file(sample, load_table({"deterministic": {"絕不出現": "x"}}),
                          s2t=False, always=True)
    sidecar = json.loads(Path(result["sidecar"]).read_text(encoding="utf-8"))
    assert sidecar["corrections"] == []
    assert sidecar["total_hits"] == 0


def test_without_always_an_unmatched_table_writes_nothing(sample):
    result = correct_file(sample, load_table({"deterministic": {"絕不出現": "x"}}), s2t=False)
    assert result["changed"] is False
    assert result["sidecar"] is None
    assert not sample.with_name("lecture.corrections.json").exists()


def test_missing_opencc_warns_once_per_process_and_marks_the_sidecar(sample, monkeypatch, capsys):
    """A missing OpenCC must be visible (a warn line, once, not per-file) and
    distinguishable in the sidecar from the user's own ``--no-s2t``.
    """
    monkeypatch.setattr(corrections_mod, "_opencc_missing_warned", False)
    monkeypatch.setitem(sys.modules, "opencc", None)

    _text1, applied1 = corrections_mod.convert_simplified("佔位文字一")
    _text2, applied2 = corrections_mod.convert_simplified("佔位文字二")
    assert applied1 is False
    assert applied2 is False

    printed = capsys.readouterr().out
    assert printed.count("opencc not installed") == 1
    assert "opencc-python-reimplemented" in printed

    correct_file(sample, load_table(TABLE), s2t=True)
    sidecar = json.loads(
        sample.with_name("lecture.corrections.json").read_text(encoding="utf-8")
    )
    assert sidecar["s2t"]["applied"] is False
    assert sidecar["s2t"]["skipped_reason"] == "opencc missing"


def test_no_s2t_marks_the_sidecar_disabled_not_missing(sample):
    """``--no-s2t`` and a missing OpenCC both leave ``applied: False`` but must
    not look identical: this one is a deliberate choice, not a degradation.
    """
    correct_file(sample, load_table(TABLE), s2t=False)
    sidecar = json.loads(
        sample.with_name("lecture.corrections.json").read_text(encoding="utf-8")
    )
    assert sidecar["s2t"]["applied"] is False
    assert sidecar["s2t"]["skipped_reason"] == "disabled"


def test_build_sidecar_is_pure_and_shaped_as_documented():
    payload = build_sidecar(
        "a.srt", "a.srt", "a.raw.srt",
        [{"heard": "x", "correct": "y", "count": 2, "source": "deterministic"}],
        s2t_applied=False, generated_at="2026-09-21T00:00:00+08:00",
    )
    assert payload["generated_at"] == "2026-09-21T00:00:00+08:00"
    assert payload["s2t"] == {"applied": False, "converter": None}
    assert payload["corrections"][0]["count"] == 2


# -- through the transcribe stage -----------------------------------------
class FakeEngine(Engine):
    """Replays a fixed cue list; never touches audio, a model or the network."""

    meta = EngineMeta(
        name="fake", local=True, needs_gpu=False,
        has_timestamps=True, default_model="none",
    )

    def __init__(self, cues=None, **options):
        super().__init__(**options)
        self.cues = cues or [
            Cue(0.0, 2.0, "佔位文字 %s 一" % HEARD),
            Cue(2.0, 4.0, "佔位文字 %s 二" % HEARD),
            Cue(4.0, 6.0, "佔位文字沒有誤聽詞"),
        ]
        self.calls = []

    def check(self) -> None:
        return None

    def transcribe(self, audio, lang):
        self.calls.append((Path(audio), lang))
        return list(self.cues)


def fake_extract(source, destination, **kwargs):
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_bytes(b"RIFF")
    return Path(destination)


def test_transcribe_stage_writes_srt_raw_and_sidecar(tmp_path):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"not a real video")
    result = pipeline.transcribe_video(
        video, "zh", FakeEngine(), pairs=load_table(TABLE),
        s2t=False, workdir=tmp_path / "work", extract=fake_extract,
    )
    assert result.srt == tmp_path / "lecture.srt"
    assert result.raw == tmp_path / "lecture.raw.srt"
    assert result.sidecar == tmp_path / "lecture.corrections.json"
    for path in result.outputs():
        assert path.is_file(), path
    assert HEARD in result.raw.read_text(encoding="utf-8")
    assert HEARD not in result.srt.read_text(encoding="utf-8")
    assert result.corrections[0]["count"] == 2


def test_transcribe_stage_with_neither_a_table_nor_s2t_writes_only_the_srt(tmp_path):
    """Nothing to apply means nothing to audit: one file, no raw, no sidecar."""
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"x")
    result = pipeline.transcribe_video(
        video, "zh", FakeEngine(), s2t=False,
        workdir=tmp_path / "work", extract=fake_extract,
    )
    assert result.srt.is_file()
    assert result.raw is None
    assert result.sidecar is None
    assert not (tmp_path / "lecture.raw.srt").exists()


def test_transcribe_stage_runs_the_s2t_pass_without_a_correction_table(tmp_path):
    """Conversion is on by default whether or not a table was supplied.

    The stage used to gate the whole correction block on a table being present,
    so a run without ``--corrections`` skipped simplified-to-traditional
    conversion silently. That went unnoticed while every engine happened to emit
    traditional Chinese; qwen3_asr emits simplified, so the gap reached the
    subtitles with nothing in the audit trail to say conversion never ran.
    """
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"x")
    result = pipeline.transcribe_video(
        video, "zh", FakeEngine(), workdir=tmp_path / "work", extract=fake_extract
    )
    assert result.srt.is_file()
    assert result.raw == tmp_path / "lecture.raw.srt"
    assert result.sidecar == tmp_path / "lecture.corrections.json"
    payload = json.loads(result.sidecar.read_text(encoding="utf-8"))
    # No table was supplied, so the sidecar records that honestly rather than
    # naming one, and the s2t block is what the file is there to report.
    assert payload["corrections_table"] is None
    assert payload["corrections"] == []
    assert "applied" in payload["s2t"]


def test_the_s2t_conversion_actually_reaches_the_srt_without_a_table(tmp_path, monkeypatch):
    """Not just that the pass runs: that its output is what lands on disk.

    OpenCC is optional, so the converter is stubbed here; asserting on real
    conversion would make this test skip on exactly the machines that most need
    it to fail loudly.
    """
    monkeypatch.setattr(
        corrections_mod, "convert_simplified", lambda text: (text.replace("佔位", "轉換"), True)
    )
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"x")
    result = pipeline.transcribe_video(
        video, "zh", FakeEngine(), workdir=tmp_path / "work", extract=fake_extract
    )
    assert "轉換文字" in result.srt.read_text(encoding="utf-8")
    assert "佔位文字" in result.raw.read_text(encoding="utf-8")
    payload = json.loads(result.sidecar.read_text(encoding="utf-8"))
    assert payload["s2t"]["applied"] is True


def test_transcribe_stage_skips_an_existing_srt_unless_forced(tmp_path):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"x")
    (tmp_path / "lecture.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nold\n",
                                          encoding="utf-8", newline="\n")
    engine = FakeEngine()
    result = pipeline.transcribe_video(
        video, "zh", engine, workdir=tmp_path / "work", extract=fake_extract
    )
    assert result.skipped is True
    assert engine.calls == []
    assert "old" in (tmp_path / "lecture.srt").read_text(encoding="utf-8")

    forced = pipeline.transcribe_video(
        video, "zh", engine, force=True,
        workdir=tmp_path / "work", extract=fake_extract,
    )
    assert forced.skipped is False
    assert "old" not in forced.srt.read_text(encoding="utf-8")


def test_chunked_transcription_shifts_each_window_onto_the_real_clock(tmp_path):
    video = tmp_path / "long.mp4"
    video.write_bytes(b"x")
    engine = FakeEngine(cues=[Cue(0.0, 5.0, "window"), Cue(5.0, 10.0, "window")])
    cues = pipeline.transcribe_audio(
        engine, video, "zh", tmp_path / "work",
        chunk_sec=60.0, duration_sec=150.0, extract=fake_extract,
    )
    assert [round(c.start, 3) for c in cues] == [0.0, 5.0, 60.0, 65.0, 120.0, 125.0]
    assert len(engine.calls) == 3


def test_a_duration_just_over_a_multiple_of_the_window_plans_no_sliver(tmp_path):
    """Container durations carry milliseconds, so this is the ordinary case.

    A 6-minute clip probes at 360.033 s, which used to plan a fourth window of
    33 ms at 360 s. Qwen3-ASR raises "Padding size should be less than the
    corresponding input dimension" on a window that short, so the whole run died
    at the very last step after every real window had already been transcribed.
    """
    assert audio_mod.plan_chunks(360.033, 120.0) == [0.0, 120.0, 240.0]
    assert audio_mod.plan_chunks(480.04, 240.0) == [0.0, 240.0]
    # A tail worth transcribing is still planned.
    assert audio_mod.plan_chunks(365.0, 120.0) == [0.0, 120.0, 240.0, 360.0]


def test_a_recording_shorter_than_the_minimum_tail_still_gets_one_window(tmp_path):
    """The tail guard must never leave a recording with nothing to transcribe."""
    assert audio_mod.plan_chunks(0.4, 240.0) == [0.0]


def test_the_sliver_window_is_never_handed_to_the_engine(tmp_path):
    video = tmp_path / "long.mp4"
    video.write_bytes(b"x")
    engine = FakeEngine(cues=[Cue(0.0, 5.0, "window")])
    pipeline.transcribe_audio(
        engine, video, "zh", tmp_path / "work",
        chunk_sec=120.0, duration_sec=360.033, extract=fake_extract,
    )
    assert len(engine.calls) == 3


def test_a_wav_input_is_handed_to_the_engine_directly(tmp_path):
    """No re-encode pass for audio that already matches the contract."""
    source = tmp_path / "already.wav"
    source.write_bytes(b"RIFF")
    engine = FakeEngine()

    def explode(*args, **kwargs):
        raise AssertionError("re-extracted an audio file that was already WAV")

    cues = pipeline.transcribe_audio(
        engine, source, "zh", tmp_path / "work", extract=explode
    )
    assert cues
    assert engine.calls[0][0] == source
