"""Fixed-input, fixed-output tests for the procedures made permanent in this port.

Everything here runs with no ffmpeg, no GPU, no model and no network. Where a
frame is needed it is synthesised with Pillow; where an ASR probe is needed a
fake engine returns canned cues through the real Engine interface.
"""

from __future__ import annotations

import json

import pytest

from lecture2notes.engines.base import Cue, Engine, EngineMeta
from lecture2notes.engines.calibrate import (
    apply_offset,
    build_report,
    calibrate,
    drift_span,
    fit_linear,
    has_drift,
    measure_offsets,
    offset_at,
    summarize_probe,
    transcribe_probes,
)
from lecture2notes.frames.interval import (
    SIGNATURE_SIZE,
    is_duplicate,
    keep_frame,
    mean_abs_diff,
    plan_marks,
    select_signatures,
    signature_from_image,
    signature_from_pixels,
)
from lecture2notes.outputs.hub import apply_titles, card_sort_key, load_titles, parse_name
from lecture2notes.schema.builder import build_document, contiguous
from lecture2notes.schema.condense import bucket_cues, condense_text, format_label

PIXELS = SIGNATURE_SIZE[0] * SIGNATURE_SIZE[1]  # 64 x 36 = 2304


# -- offset measurement ----------------------------------------------------

#: Three probe medians measured on one long recording: the offset shrinks as the
#: file plays, which is the drift a single constant shift cannot express.
DRIFTING_PROBES = [(300.0, 3.14), (5733.0, 2.23), (10548.0, 0.86)]


def _official_cues():
    return [
        Cue(100.0, 105.0, "這是第一段用來對時間的測試內容"),
        Cue(105.0, 110.0, "第二段的文字完全不同以免誤判"),
        Cue(110.0, 115.0, "第三段又是另一組不重複的字串"),
    ]


def test_measure_offsets_recovers_a_known_constant_shift():
    official = _official_cues()
    # The probe window starts at 98 s; the official track claims 100 s, so every
    # measurement must come back as exactly +2.
    probe = [
        Cue(0.0, 5.0, official[0].text),
        Cue(5.0, 10.0, official[1].text),
        Cue(10.0, 15.0, official[2].text),
    ]

    offsets = measure_offsets(probe, official, base_sec=98.0)

    assert offsets == [2.0, 2.0, 2.0]
    assert summarize_probe(98.0, offsets).median_offset == 2.0


def test_measure_offsets_skips_short_and_unmatched_cues():
    official = _official_cues()
    probe = [
        Cue(0.0, 1.0, "太短"),
        Cue(1.0, 6.0, "abcdefghijklmnopqrstuvwxyz"),
        Cue(6.0, 11.0, official[1].text),
    ]

    # Only the third cue is locatable; the official track claims 105 s where the
    # probe puts it at 104 s, so exactly one measurement of +1 comes back.
    assert measure_offsets(probe, official, base_sec=98.0) == [1.0]


def test_summarize_probe_marks_an_unreliable_probe():
    assert summarize_probe(300.0, []).reliable is False
    assert summarize_probe(300.0, [1.0, 2.0]).reliable is False
    assert summarize_probe(300.0, [1.0, 2.0, 3.0]).reliable is True
    assert summarize_probe(300.0, []).to_dict()["median_offset"] is None


# -- linear calibration ----------------------------------------------------

def test_fit_linear_over_three_drifting_probes():
    a, b = fit_linear(DRIFTING_PROBES)

    assert b < 0  # the offset shrinks as the recording plays
    assert abs(offset_at(a, b, 300.0) - 3.14) < 0.1
    # The fit smooths the measurements rather than passing through them, so the
    # far end is allowed to sit further from its probe than the near end.
    assert offset_at(a, b, 300.0) > offset_at(a, b, 5733.0) > offset_at(a, b, 10548.0)
    assert abs(offset_at(a, b, 10548.0) - 0.86) < 0.5


def test_fit_linear_degenerates_to_a_constant_on_one_point():
    a, b = fit_linear([(300.0, 1.5)])

    assert b == 0.0
    assert offset_at(a, b, 9999.0) == 1.5


def test_drift_is_declared_only_past_the_threshold():
    assert round(drift_span([m for _t, m in DRIFTING_PROBES]), 2) == 2.28
    assert has_drift([m for _t, m in DRIFTING_PROBES]) is True
    assert has_drift([1.44, 0.80, 0.96]) is False


def test_apply_offset_moves_times_only_and_keeps_the_text():
    cues = [Cue(100.0, 105.0, "一"), Cue(105.0, 110.0, "二")]

    shifted = apply_offset(cues, 2.0, 0.0)

    assert [cue.text for cue in shifted] == [cue.text for cue in cues]
    assert [(cue.start, cue.end) for cue in shifted] == [(98.0, 103.0), (103.0, 108.0)]


def test_apply_offset_widens_a_zero_length_cue_to_the_floor():
    widened = apply_offset([Cue(10.0, 10.0, "x"), Cue(20.0, 20.1, "y")], 0.0, 0.0)

    assert widened[0].duration() == pytest.approx(0.3)
    assert widened[1].duration() == pytest.approx(0.3)
    assert all(cue.duration() >= 0.3 for cue in widened)


def test_apply_offset_never_produces_a_negative_start():
    assert apply_offset([Cue(1.0, 2.0, "x")], 5.0, 0.0)[0].start == 0.0


def test_build_report_shape():
    probes = [summarize_probe(at, [median]) for at, median in DRIFTING_PROBES]
    a, b = fit_linear(DRIFTING_PROBES)
    report = build_report(probes, a, b)

    assert set(report) == {"probes", "fit", "drift", "span"}
    assert set(report["probes"][0]) == {"at_sec", "n_points", "median_offset", "min", "max"}
    assert set(report["fit"]) == {"a", "b"}
    assert report["drift"] is True


# -- calibration through a fake engine ------------------------------------

class FakeEngine(Engine):
    """Returns canned cues, so calibration can be tested with no model at all."""

    meta = EngineMeta(name="fake", local=True, needs_gpu=False, has_timestamps=True)

    def __init__(self, cues_by_window):
        super().__init__()
        self.cues_by_window = cues_by_window
        self.calls = []

    def check(self) -> None:
        return None

    def transcribe(self, audio, lang):
        self.calls.append((str(audio), lang))
        return self.cues_by_window[str(audio)]


def test_calibrate_through_an_injected_engine(tmp_path):
    official = _official_cues()
    probe_cues = [
        Cue(0.0, 5.0, official[0].text),
        Cue(5.0, 10.0, official[1].text),
        Cue(10.0, 15.0, official[2].text),
    ]
    windows = [(98.0, tmp_path / "p1.wav"), (98.0, tmp_path / "p2.wav"), (98.0, tmp_path / "p3.wav")]
    engine = FakeEngine({str(path): probe_cues for _at, path in windows})

    transcribed = transcribe_probes(engine, windows, "zh")
    corrected, report = calibrate(official, transcribed)

    assert [call[1] for call in engine.calls] == ["zh", "zh", "zh"]
    assert [cue.text for cue in corrected] == [cue.text for cue in official]
    assert corrected[0].start == pytest.approx(98.0)
    assert report["drift"] is False
    assert report["fit"]["b"] == 0.0


def test_calibrate_refuses_to_write_with_too_few_reliable_probes():
    official = _official_cues()
    windows = [(98.0, [Cue(0.0, 1.0, "短")]) for _ in range(3)]

    with pytest.raises(ValueError, match="reliable probes"):
        calibrate(official, windows)


# -- interval sampling and deduplication ----------------------------------

def test_plan_marks_walks_the_recording_at_a_fixed_interval():
    assert plan_marks(100, 45) == [0, 45, 90]
    assert plan_marks(90, 45) == [0, 45]
    with pytest.raises(ValueError):
        plan_marks(100, 0)


def test_is_duplicate_boundary_is_keep_at_exactly_the_threshold():
    assert is_duplicate(3.99, 4.0) is True    # below the threshold: dropped
    assert is_duplicate(4.00, 4.0) is False   # exactly at it: kept
    assert is_duplicate(25.3, 4.0) is False


def test_keep_frame_on_real_64x36_signatures():
    base = signature_from_pixels([100] * PIXELS)
    # Every pixel four levels brighter: mean absolute difference is exactly 4.00.
    exactly_four = signature_from_pixels([104] * PIXELS)
    # One pixel short of that: the mean lands just under 4.00.
    just_under = signature_from_pixels([104] * (PIXELS - 1) + [100])

    assert mean_abs_diff(base, exactly_four) == pytest.approx(4.0)
    assert mean_abs_diff(base, just_under) < 4.0
    assert keep_frame(base, exactly_four) is True
    assert keep_frame(base, just_under) is False
    assert keep_frame(None, base) is True


def test_select_signatures_keeps_only_the_first_of_ten_identical_frames():
    identical = [signature_from_pixels([100] * PIXELS) for _ in range(10)]

    assert select_signatures(identical) == [0]


def test_select_signatures_compares_against_the_last_kept_frame():
    signatures = [
        signature_from_pixels([100] * PIXELS),
        signature_from_pixels([102] * PIXELS),  # +2 from the last kept: dropped
        signature_from_pixels([104] * PIXELS),  # +4 from the last kept: kept
    ]

    assert select_signatures(signatures) == [0, 2]


def test_mean_abs_diff_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        mean_abs_diff([1, 2, 3], [1, 2])
    with pytest.raises(ValueError):
        signature_from_pixels([1, 2, 3])


def test_signature_from_image_reads_a_synthesised_png(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "flat.png"
    Image.new("RGB", (320, 180), (100, 100, 100)).save(path)

    signature = signature_from_image(path)

    assert len(signature) == PIXELS
    assert set(signature) == {100}


def test_signature_from_image_detects_a_changed_slide(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    first, second = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGB", (320, 180), (100, 100, 100)).save(first)
    Image.new("RGB", (320, 180), (200, 200, 200)).save(second)

    assert keep_frame(signature_from_image(first), signature_from_image(second)) is True


# -- transcript condensing -------------------------------------------------

SAMPLE_SRT = (
    "1\n00:00:05,000 --> 00:00:07,000\nfirst line\n\n"
    "2\n00:00:30,500 --> 00:00:33,000\nsecond line\n\n"
    "3\n00:01:10,000 --> 00:01:12,000\nthird line\n\n"
    "4\n01:02:03,000 --> 01:02:05,000\nmuch later\n"
)


def test_condense_text_puts_one_line_per_minute():
    assert condense_text(SAMPLE_SRT) == (
        "[  0:00] first line second line\n"
        "[  0:01] third line\n"
        "[  1:02] much later\n"
    )


def test_condense_text_honours_the_bucket_size():
    assert condense_text(SAMPLE_SRT, bucket_sec=3600) == (
        "[  0:00] first line second line third line\n"
        "[  1:00] much later\n"
    )


def test_bucket_cues_and_format_label():
    buckets = bucket_cues(SAMPLE_SRT)

    assert sorted(buckets) == [0, 1, 62]
    assert buckets[0] == ["first line", "second line"]
    assert format_label(0) == "[  0:00]"
    assert format_label(62) == "[  1:02]"


def test_condense_text_on_an_empty_transcript():
    assert condense_text("") == ""


# -- document builder ------------------------------------------------------

BUILDER_SEGMENTS = [
    (0, "Opening", "the opening summary", ["one", "two"]),
    (60, "Middle", "the middle summary", ["three"]),
    (120, "Closing", "the closing summary", ["four"]),
]
BUILDER_FRAMES = [(10.0, "frames/demo-0010.png"), (70.0, "frames/demo-0110.png")]


def test_build_document_produces_a_fixed_structure():
    document = build_document(
        "demo", "an overall summary", ["a", "b"], BUILDER_SEGMENTS, 180.0, BUILDER_FRAMES
    )

    assert document["stem"] == "demo"
    assert document["duration_sec"] == 180
    assert document["takeaways_zh"] == ["a", "b"]
    assert [s["index"] for s in document["segments"]] == [1, 2, 3]
    assert [(s["start_sec"], s["end_sec"]) for s in document["segments"]] == [
        (0, 60), (60, 120), (120, 180)
    ]
    assert [s["start_time"] for s in document["segments"]] == [
        "00:00:00", "00:01:00", "00:02:00"
    ]
    assert document["segments"][2]["end_time"] == "00:03:00"
    assert contiguous(document) is True


def test_build_document_lets_a_frameless_segment_inherit_the_previous_frame():
    segments = build_document(
        "demo", "s", ["a"], BUILDER_SEGMENTS, 180.0, BUILDER_FRAMES
    )["segments"]

    assert segments[0]["frames"] == ["frames/demo-0010.png"]
    assert segments[1]["frames"] == ["frames/demo-0110.png"]
    # No frame falls inside the closing segment, so it shows the last one that
    # was on screen, and reports no frames of its own.
    assert segments[2]["frame"] == "frames/demo-0110.png"
    assert segments[2]["frames"] == []
    assert segments[0]["frame"] == "frames/demo-0010.png"


def test_build_document_is_byte_identical_across_runs():
    def dump():
        return json.dumps(
            build_document("demo", "s", ["a"], BUILDER_SEGMENTS, 180.0, BUILDER_FRAMES),
            ensure_ascii=False, sort_keys=True,
        )

    assert dump() == dump()


def test_build_document_without_a_duration_pads_the_last_segment():
    document = build_document("demo", "s", ["a"], BUILDER_SEGMENTS, 0.0)

    assert document["segments"][-1]["end_sec"] == 120 + 300
    assert document["segments"][-1]["frame"] is None


def test_build_document_requires_at_least_one_segment():
    with pytest.raises(ValueError):
        build_document("demo", "s", ["a"], [], 60.0)


# -- _titles.json card overrides -------------------------------------------

def test_parse_name_reads_a_structured_filename():
    assert parse_name("11506-09-王醫師-乳房攝影") == {
        "no": "09", "speaker": "王醫師", "topic": "乳房攝影"
    }
    assert parse_name("11506-03-plain topic") == {
        "no": "03", "speaker": "", "topic": "plain topic"
    }
    assert parse_name("random name") == {"no": "", "speaker": "", "topic": "random name"}


def test_load_titles_returns_empty_without_the_file(tmp_path):
    assert load_titles(tmp_path) == {}

    (tmp_path / "_titles.json").write_text("{ broken", encoding="utf-8")
    assert load_titles(tmp_path) == {}


def test_titles_json_overrides_the_derived_card(tmp_path):
    (tmp_path / "_titles.json").write_text(
        json.dumps({"recording-2": {"no": "01", "speaker": "Guest", "topic": "Real topic"}}),
        encoding="utf-8",
    )
    titles = load_titles(tmp_path)

    assert apply_titles("recording-2", titles) == {
        "no": "01", "speaker": "Guest", "topic": "Real topic"
    }
    assert apply_titles("recording-9", titles) == {
        "no": "", "speaker": "", "topic": "recording-9"
    }


def test_titles_json_may_override_a_single_field():
    card = apply_titles("11506-09-王醫師-old", {
        "11506-09-王醫師-old": {"topic": "corrected"}
    })

    assert card == {"no": "09", "speaker": "王醫師", "topic": "corrected"}


def test_card_sort_key_puts_overridden_numbers_in_order():
    titles = {"zeta": {"no": "01"}, "alpha": {"no": "02"}}
    cards = [
        dict(apply_titles(stem, titles), stem=stem) for stem in ("alpha", "zeta", "unnumbered")
    ]

    assert [card["stem"] for card in sorted(cards, key=card_sort_key)] == [
        "zeta", "alpha", "unnumbered"
    ]
