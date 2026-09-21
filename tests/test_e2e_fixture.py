"""The whole pipeline, on the real recording, with a real ASR model.

Every other test in this repository runs on synthesised inputs: flat-colour
PNGs, a silent video, a subtitle written by hand. That is the right default --
it is fast, deterministic and needs nothing installed -- but it cannot catch the
class of bug that only appears when real bytes go through the real stages. The
first time this file was run it found one: faster_whisper picked a CUDA device
that could not load cuBLAS and the run died, on a machine the engine claims to
support.

So this is the one test that runs `l2n` for real:

    transcribe (faster-whisper tiny) -> frames (scene) -> a canonical v2
    document -> render -> check transcribe / frames / json / note

The document in the middle is the LLM's job in production, and no model is
called here. `_document_for` writes the same shape a language model would, with
its times fitted to the 25-second clip and its frames taken from the manifest
the capture stage actually produced.

It is marked `e2e` and excluded from the default run, because it needs ffmpeg
and faster-whisper and downloads ~75 MB of model weights the first time. When
either is missing it skips and says which.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Dict, List

import pytest

from conftest import run_cli
from fixture_paths import CLIP_DURATION_SEC, FIXTURE_CLIP
from lecture2notes import exit_codes

pytestmark = pytest.mark.e2e

SRC = Path(__file__).resolve().parents[1] / "src"

STEM = "clip"
LANG = "en"
ENGINE = "faster_whisper"
#: The smallest official Whisper model. Accuracy is beside the point here: the
#: assertions are about the pipeline, not about the words it heard.
MODEL = "tiny"

#: Where the clip cuts from the credits card to the title card, per the
#: fixture's README. Scene capture has to find it.
CUT_WINDOW = (9.0, 13.0)

#: `<stage>: N errors, M warnings`, the last line every check prints.
SUMMARY_RE = re.compile(r"^(?P<stage>\w+): (?P<errors>\d+) errors, (?P<warnings>\d+) warnings",
                        re.M)

#: Both are acceptable outcomes for a check: 0 clean, 1 warnings only. 2 is not.
CLEAN_CODES = (exit_codes.OK, exit_codes.WARN)


def _require_tools() -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not on PATH; the pipeline cannot extract audio or frames")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is not on PATH; the pipeline cannot measure the clip")
    if find_spec("faster_whisper") is None:
        pytest.skip("faster-whisper is not installed: pip install faster-whisper")
    if not FIXTURE_CLIP.is_file():
        pytest.skip("the fixture clip is missing: %s" % FIXTURE_CLIP)


def _cli(work: Path, *args: str):
    """`l2n <args>` in *work*, against this checkout."""
    return run_cli(list(args), cwd=work, env={"PYTHONPATH": str(SRC)})


def _segment(
    index: int, start: float, end: float, frame: str, title: str, summary: str,
    bullets: List[str],
) -> Dict[str, Any]:
    def clock(seconds: float) -> str:
        whole = int(seconds)
        return "%02d:%02d:%02d" % (whole // 3600, (whole // 60) % 60, whole % 60)

    return {
        "index": index,
        "start_time": clock(start),
        "end_time": clock(end),
        "start_sec": start,
        "end_sec": end,
        "title": title,
        "summary_zh": summary,
        "bullets_zh": [
            {"text": text, "t": start if position == 0 else None, "kind": "synthesis"}
            for position, text in enumerate(bullets)
        ],
        "quotes_zh": [],
        "frame": frame,
        "frames": [frame],
        "frame_ocr": [],
        "editorial_notes_zh": [],
    }


def _document_for(manifest_rows: List[Dict[str, Any]], duration: float) -> Dict[str, Any]:
    """The canonical v2 document a language model would write for this clip.

    Three segments over the clip's own length, each pointing at a frame the
    capture stage really produced. The last segment ends on floor(duration_sec),
    which is the contiguity rule `check json` enforces.
    """
    frames = [str(row["frame"]) for row in manifest_rows]
    assert frames, "the capture stage produced no frames to build a document from"

    def frame_at(position: int) -> str:
        return frames[min(position, len(frames) - 1)]

    end = float(math.floor(duration))
    first, second = end * 0.32, end * 0.68
    return {
        "schema_version": "2.0",
        "stem": STEM,
        "title": "測試片段：著作權課程第 12 講開場",
        "duration_sec": duration,
        "source": {
            "video": "%s.mp4" % STEM,
            "subtitle": {
                "path": "%s.srt" % STEM,
                "origin": "asr",
                "engine": ENGINE,
                "lang": LANG,
                "offset_model": None,
            },
        },
        "profile": "generic",
        "overall_summary_zh": (
            "本文件是端對端測試用的正式 JSON，對應一段 25 秒的課程開場片段。"
            "片段依序是黑色片頭、製作團隊字卡、講次標題卡，最後講者入鏡開場。"
            "內容只到開場白為止，沒有任何實質授課內容，因此本摘要也只描述畫面"
            "與語音的結構，不對課程主題作任何陳述。這份文件的用途是讓抓圖、"
            "算繪與四個階段驗收在真實素材上跑過一次。"
        ),
        "takeaways_zh": [
            "本片段是課程開場，不含實質授課內容",
            "片中至少有一次明確的畫面切換，可供場景偵測驗證",
            "音軌為真實人聲，可供轉錄階段驗證",
            "三個段落涵蓋整段影片，沒有空隙",
            "每個段落都指向抓圖階段實際產生的影格",
            "本文件由測試寫成，不是語言模型的輸出",
        ],
        "segments": [
            _segment(
                1, 0.0, first, frame_at(0), "片頭",
                "影片開頭的片頭畫面，沒有語音內容。",
                ["開頭為片頭畫面", "此段用來驗證第一個影格"],
            ),
            _segment(
                2, first, second, frame_at(1), "字卡",
                "畫面切到字卡，列出課程與製作團隊資訊。",
                ["畫面切換到字卡", "此段涵蓋片中的一次場景切換"],
            ),
            _segment(
                3, second, end, frame_at(2), "講者開場",
                "講者入鏡並開始自我介紹，預告本講主題。",
                ["講者入鏡開場", "此段結束於整段影片的尾端"],
            ),
        ],
        "corrections": [],
        "unverified_terms": [],
    }


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory) -> Dict[str, Any]:
    """Run the whole pipeline once, and hand every test the results.

    Module-scoped because transcription is the slow part and none of the
    assertions below mutate the folder.
    """
    _require_tools()
    work = tmp_path_factory.mktemp("e2e")
    shutil.copy2(FIXTURE_CLIP, work / ("%s.mp4" % STEM))

    results: Dict[str, Any] = {"work": work}
    results["transcribe"] = _cli(
        work, "transcribe", "%s.mp4" % STEM,
        "--lang", LANG, "--engine", ENGINE, "--model", MODEL,
    )
    results["frames"] = _cli(work, "frames", "%s.mp4" % STEM, "--mode", "scene")

    manifest_path = work / ("%s.frames.json" % STEM)
    if results["transcribe"].returncode == exit_codes.OK and manifest_path.is_file():
        rows = json.loads(manifest_path.read_text(encoding="utf-8"))
        document = _document_for(rows, CLIP_DURATION_SEC)
        (work / ("%s.json" % STEM)).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
        results["document"] = document
        results["render"] = _cli(work, "render", "%s.json" % STEM)
        results["viewer"] = _cli(work, "viewer", "%s.json" % STEM)
        for stage, target in (
            ("transcribe", "%s.srt" % STEM),
            ("frames", "%s.frames.json" % STEM),
            ("json", "%s.json" % STEM),
            ("note", "%s.json" % STEM),
        ):
            results["check_%s" % stage] = _cli(work, "check", stage, target)
        results["check_all"] = _cli(
            work, "check", "--all", STEM, "--report", "report.json"
        )
    return results


def _summary(result) -> Dict[str, int]:
    match = SUMMARY_RE.search(result.stdout)
    assert match, "no check summary line in:\n%s\n%s" % (result.stdout, result.stderr)
    return {
        "errors": int(match.group("errors")),
        "warnings": int(match.group("warnings")),
    }


def _assert_clean(result, label: str) -> None:
    assert result.returncode in CLEAN_CODES, (
        "%s exited %d\nstdout:\n%s\nstderr:\n%s"
        % (label, result.returncode, result.stdout, result.stderr)
    )
    counts = _summary(result)
    assert counts["errors"] == 0, (
        "%s reported %d errors:\n%s" % (label, counts["errors"], result.stdout)
    )


# -- stages ----------------------------------------------------------------
def test_transcribe_produces_a_subtitle_with_real_speech_in_it(pipeline):
    result = pipeline["transcribe"]
    assert result.returncode == exit_codes.OK, result.stdout + result.stderr

    subtitle = (pipeline["work"] / ("%s.srt" % STEM)).read_text(encoding="utf-8")
    cues = [block for block in subtitle.split("\n\n") if block.strip()]

    assert cues, "the subtitle has no cues"
    spoken = "".join(
        line for block in cues for line in block.splitlines()[2:]
    ).strip()
    assert len(spoken) >= 20, "transcribed text is suspiciously short: %r" % spoken


def test_scene_capture_finds_the_documented_cut(pipeline):
    result = pipeline["frames"]
    assert result.returncode == exit_codes.OK, result.stdout + result.stderr

    rows = json.loads(
        (pipeline["work"] / ("%s.frames.json" % STEM)).read_text(encoding="utf-8")
    )
    times = [float(row["timestamp_sec"]) for row in rows]

    assert rows, "scene capture kept no frames"
    assert any(CUT_WINDOW[0] <= second <= CUT_WINDOW[1] for second in times), (
        "no frame near the cut the fixture README documents (%.1f-%.1fs); got %s"
        % (CUT_WINDOW[0], CUT_WINDOW[1], times)
    )
    for row in rows:
        assert (pipeline["work"] / row["frame"]).is_file()


def test_render_writes_a_skeleton_note(pipeline):
    result = pipeline["render"]
    assert result.returncode == exit_codes.OK, result.stdout + result.stderr

    note = pipeline["work"] / ("%s.v4.md" % STEM)
    raw = note.read_bytes()

    assert raw[:3] != b"\xef\xbb\xbf", "the note must not carry a BOM"
    assert b"\r\n" not in raw, "the note must be LF only"
    assert "# Summary" in raw.decode("utf-8")


# -- the four acceptance checks --------------------------------------------
@pytest.mark.parametrize("stage", ["transcribe", "frames", "json", "note"])
def test_every_stage_check_passes_on_the_real_run(pipeline, stage: str):
    _assert_clean(pipeline["check_%s" % stage], "check %s" % stage)


def test_check_all_agrees_and_writes_a_report(pipeline):
    result = pipeline["check_all"]
    _assert_clean(result, "check --all")

    report = json.loads(
        (pipeline["work"] / "report.json").read_text(encoding="utf-8")
    )

    assert report["stem"] == STEM
    assert set(report["stages"]) == {"transcribe", "frames", "json", "note"}
    assert report["summary"]["errors"] == 0, report["stages"]


# -- the second run --------------------------------------------------------
def test_viewer_builds_a_time_synced_page(pipeline):
    result = pipeline["viewer"]
    assert result.returncode == exit_codes.OK, result.stdout + result.stderr

    page = (pipeline["work"] / ("%s.viewer.html" % STEM)).read_text(encoding="utf-8")

    assert "<video" in page
    assert "%s.mp4" % STEM in page


def test_a_second_run_skips_every_stage_that_already_has_output(pipeline):
    """`l2n run` is resumable: nothing is recomputed, nothing is overwritten."""
    if find_spec("rapidocr_onnxruntime") is None:
        pytest.skip("rapidocr is not installed; `l2n run` cannot reach its OCR stage")
    work = pipeline["work"]
    before = {
        name: (work / name).stat().st_mtime_ns
        for name in ("%s.srt" % STEM, "%s.frames.json" % STEM, "%s.v4.md" % STEM)
    }

    result = _cli(
        work, "run", "%s.mp4" % STEM,
        "--lang", LANG, "--engine", ENGINE, "--model", MODEL,
    )

    assert result.returncode in CLEAN_CODES, result.stdout + result.stderr
    for stage in ("transcribe", "frames", "render", "viewer"):
        assert "[%s] skip (exists)" % stage in result.stdout, (
            "%s did not skip on the second run:\n%s" % (stage, result.stdout)
        )
    for name, stamp in before.items():
        assert (work / name).stat().st_mtime_ns == stamp, "%s was rewritten" % name


def test_the_note_check_says_the_skeleton_is_still_a_skeleton(pipeline):
    """The pipeline stops at `l2n render`, so the note is not written yet.

    Task 15.10: every rule about shape passes on a skeleton, which is why R10
    exists. A real run must therefore end with R10 on every segment and a
    non-zero `unexpanded_segments` ratio -- and still be a clean check, because
    a skeleton is a legitimate intermediate state, not an error.
    """
    result = pipeline["check_note"]
    _assert_clean(result, "check note")

    assert "warn R10 segment 1" in result.stdout, result.stdout
    assert "note: unexpanded_segments=" in result.stdout
    assert "note: ai_draft_remaining=" in result.stdout
