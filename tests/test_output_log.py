"""``--log FILE``: everything to the file, only what needs reading to the console."""

from __future__ import annotations

from pathlib import Path

from lecture2notes import _out
from lecture2notes.engines.audio import build_extract_command
from lecture2notes.engines.whisper_cpp import WhisperCppEngine


def test_progress_and_stage_lines_go_to_the_log_only(tmp_path: Path, capsys) -> None:
    log = tmp_path / "talk.l2n.log"
    _out.configure(log_path=str(log))
    try:
        progress = _out.Progress("frames", 3, interval=0)
        for _ in range(3):
            progress.advance()
        _out.stage("frames", "3 kept")
        _out.say("info", "detail")
        _out.warn("something to read")
        _out.ok("frames: talk.frames.json")
        _out.line("result line")
    finally:
        _out.reset()
    printed = capsys.readouterr().out
    assert "[frames] 1/3" not in printed and "3 kept" not in printed and "detail" not in printed
    assert "[warn] something to read" in printed
    assert "[ok] frames: talk.frames.json" in printed and "result line" in printed
    written = log.read_text(encoding="utf-8")
    for text in ("[frames] 3/3", "[frames] 3 kept", "[info] detail", "[warn] something", "result line"):
        assert text in written, text
    assert written.startswith("=== ")


def test_the_log_is_appended_not_replaced(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    for word in ("first", "second"):
        _out.configure(log_path=str(log))
        _out.stage("s", word)
        _out.reset()
    text = log.read_text(encoding="utf-8")
    assert "first" in text and "second" in text


def test_the_cli_accepts_log_after_the_subcommand(tmp_path: Path) -> None:
    from conftest import run_cli

    log = tmp_path / "check.log"
    result = run_cli(["check", "json", str(tmp_path / "missing.json"), "--log", str(log)],
                     env={"PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 2
    assert log.is_file() and "missing.json" in log.read_text(encoding="utf-8")


def test_ffmpeg_prints_no_stats_and_whisper_cpp_no_segments(tmp_path: Path) -> None:
    argv = build_extract_command("ffmpeg", tmp_path / "a.mp4", tmp_path / "a.wav")
    assert "-nostats" in argv and argv[argv.index("-loglevel") + 1] == "error"
    command = WhisperCppEngine(binary="whisper-cli", model="m.bin").build_command(
        tmp_path / "a.wav", "zh", tmp_path / "a"
    )
    assert "-np" in command
