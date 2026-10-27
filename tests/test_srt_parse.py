"""Subtitle parsing: the formats real files arrive in, and caption de-duplication."""

from __future__ import annotations

import pytest

from lecture2notes.engines.base import Cue, dedupe_cues, parse_srt, parse_srt_text


def texts(cues):
    return [cue.text for cue in cues]


def test_crlf_bom_and_unpadded_hour(tmp_path):
    path = tmp_path / "talk.srt"
    path.write_bytes(
        b"\xef\xbb\xbf1\r\n1:02:03,400 --> 1:02:05,000\r\nhello\r\n\r\n"
        b"2\r\n1:02:05,000 --> 1:02:07,5\r\nworld\r\n"
    )
    cues = parse_srt(path)
    assert cues == [
        Cue(start=3723.4, end=3725.0, text="hello"),
        Cue(start=3725.0, end=3727.5, text="world"),
    ]


def test_vtt_header_ids_notes_short_clock_tags_and_entities():
    text = (
        "WEBVTT\nKind: captions\nLanguage: en\n\n"
        "STYLE\n::cue { color: lime }\n\n"
        "NOTE this block\nis not a cue\n\n"
        "intro\n00:01.500 --> 00:03.000 align:start position:10%\n"
        "<v Dr A><c.yellow>Hello</c> &amp; <00:02.000>welcome</v>\n\n"
        "00:03.000 --> 00:05.000\nlesion &lt;5 mm\n"
    )
    cues = parse_srt_text(text)
    assert [(c.start, c.end) for c in cues] == [(1.5, 3.0), (3.0, 5.0)]
    assert texts(cues) == ["Hello & welcome", "lesion <5 mm"]


def test_literal_angle_bracket_survives_in_srt():
    cues = parse_srt_text("1\n00:00:01,000 --> 00:00:02,000\n{\\an8}<5 mm and <i>T2</i> bright\n")
    assert texts(cues) == ["<5 mm and T2 bright"]


def test_number_inside_text_is_kept():
    cues = parse_srt_text("1\n00:00:01,000 --> 00:00:02,000\n2024\nyear\n")
    assert texts(cues) == ["2024 year"]


def test_every_cue_is_kept_by_the_parser():
    srt = "".join(
        "%d\n00:00:0%d,000 --> 00:00:0%d,000\nsame\n\n" % (n, n, n + 1) for n in range(1, 4)
    )
    assert len(parse_srt_text(srt)) == 3


def test_dedupe_merges_repeats_and_extends_end():
    cues = [Cue(0, 1, "a"), Cue(1, 2, "a"), Cue(2, 3, "b")]
    assert dedupe_cues(cues) == [Cue(0, 2, "a"), Cue(2, 3, "b")]


@pytest.mark.parametrize("rolling, expected", [
    (["one two", "two three", "three four"], ["one two", "three", "four"]),
    (["a", "a b", "a b c"], ["a", "b", "c"]),
])
def test_dedupe_strips_rolling_overlap(rolling, expected):
    cues = [Cue(n, n + 1, text) for n, text in enumerate(rolling)]
    assert texts(dedupe_cues(cues)) == expected


def test_dedupe_does_not_cut_a_shared_first_word():
    cues = [Cue(0, 1, "the liver"), Cue(1, 2, "the spleen")]
    assert texts(dedupe_cues(cues)) == ["the liver", "the spleen"]


def test_dedupe_leaves_chinese_without_spaces_alone():
    cues = [Cue(0, 1, "肝臟病灶"), Cue(1, 2, "肝臟病灶的分級")]
    assert texts(dedupe_cues(cues)) == ["肝臟病灶", "肝臟病灶的分級"]
