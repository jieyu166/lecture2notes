"""Shared helpers for running the CLI in-process and as a subprocess.

This also holds ``synthetic_lecture``: one folder carrying a complete, internally
consistent set of pipeline outputs. Until the CC-licensed fixture recording is in
the repository there is nothing to run the end-to-end acceptance checks against,
and a stage contract that is only ever tested on damaged inputs is half tested --
nothing pins the claim that a clean pipeline is clean. So the clean set is
synthesised: flat-colour PNG frames, a manifest hashed from them, the canonical
JSON fixture re-pointed at those frames, a skeleton note rendered from that JSON,
and a subtitle whose words appear nowhere in the note.

``video`` is the one piece that needs ffmpeg, so it is ``None`` when ffmpeg is
absent and the tests that need a recording skip themselves. When the real fixture
lands it replaces the bodies here, not the fixture's shape.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pytest

from lecture2notes import _out


@pytest.fixture(autouse=True)
def reset_output_flags():
    """Keep the module-level output flags from leaking between tests."""
    _out.reset()
    yield
    _out.reset()


def run_cli(
    args: Sequence[str],
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    encoding: str = "utf-8",
) -> subprocess.CompletedProcess:
    """Run `python -m lecture2notes <args>` in a child process."""
    child_env = dict(os.environ)
    if env:
        child_env.update(env)
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        [sys.executable, "-m", "lecture2notes", *args],
        cwd=str(cwd) if cwd else None,
        env=child_env,
        capture_output=True,
        text=True,
        encoding=encoding,
        errors="replace",
    )


def minimal_path(extra: Optional[Path] = None) -> str:
    """A PATH that still lets Python start but contains no pipeline binaries."""
    parts: List[str] = [str(Path(sys.executable).parent)]
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        parts.append(str(Path(system_root) / "System32"))
    if extra is not None:
        parts.insert(0, str(extra))
    return os.pathsep.join(parts)


def listdir_set(path: Path) -> set:
    return {p.name for p in path.iterdir()}


def tree_state(root: Path) -> Dict[str, tuple]:
    """Every file under *root* with its size and mtime, for a read-only proof."""
    state: Dict[str, tuple] = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            info = path.stat()
            state[str(path.relative_to(root)).replace(os.sep, "/")] = (
                info.st_size, info.st_mtime_ns
            )
    return state


# --------------------------------------------------------------------------
# synthetic_lecture: a clean, internally consistent set of pipeline outputs
# --------------------------------------------------------------------------
#: The stem every synthetic artefact shares. It starts with a date because the
#: note frontmatter derives one from the stem, and it carries a space because
#: real lecture filenames do and a fixture that avoids them proves nothing.
SYNTHETIC_STEM = "20240115 synthetic talk"

#: Subtitle text that shares no run of characters with anything the skeleton
#: note renders, so the transcript-paste rule has nothing to find in a clean
#: note. Latin, because the comparison folds width and case and a latin run
#: makes the threshold arithmetic legible.
_CUE_WORDS = (
    "qomzubrekathivlanpydsucwexfgjor",
    "vlukthemzarodniqpescybwaxgnufori",
    "drazmelphitouqvenkybcusgwoxarnif",
)


@dataclass
class SyntheticLecture:
    """One folder holding every artefact the four stage checks look at."""

    root: Path
    stem: str
    video: Optional[Path]
    subtitle: Path
    document: Path
    manifest: Path
    frames_dir: Path
    note: Path

    def path(self, suffix: str) -> Path:
        return self.root / (self.stem + suffix)


def _write_subtitle(path: Path, spans: Sequence[tuple]) -> Path:
    def stamp(seconds: float) -> str:
        whole = int(seconds)
        milli = int(round((seconds - whole) * 1000))
        return "%02d:%02d:%02d,%03d" % (
            whole // 3600, (whole // 60) % 60, whole % 60, milli
        )

    blocks = []
    for number, (start, end, text) in enumerate(spans, 1):
        blocks.append(
            "%d\n%s --> %s\n%s\n" % (number, stamp(start), stamp(end), text)
        )
    path.write_text("\n".join(blocks), encoding="utf-8", newline="\n")
    return path


def _silent_video(folder: Path, name: str) -> Optional[Path]:
    """The six-slide fixture video, with a silent audio track added.

    The audio track is not decoration: the transcribe stage extracts audio
    before it reaches the engine, and ffmpeg fails outright on a file with no
    audio stream, so a video without one cannot test the stage at all.
    """
    if shutil.which("ffmpeg") is None:
        return None
    from fixtures import synthetic

    mute = synthetic.build_lecture_video(folder, name="_mute.mp4")
    out = Path(folder) / name
    result = subprocess.run(
        [
            shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(mute),
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
            "-shortest", "-c:v", "copy", "-c:a", "aac", str(out),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    mute.unlink(missing_ok=True)
    shutil.rmtree(Path(folder) / "_slides", ignore_errors=True)
    if result.returncode != 0 or not out.is_file():
        raise RuntimeError("ffmpeg could not add a silent track: %s" % result.stderr)
    return out


def build_synthetic_lecture(
    root: Path, stem: str = SYNTHETIC_STEM, video: bool = True
) -> SyntheticLecture:
    """Write a complete lecture output set into *root* and describe it."""
    from fixtures import synthetic

    from lecture2notes.frames.manifest import record_for_file, write_manifest
    from lecture2notes.notes.render import render_skeleton

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fixture = Path(__file__).parent / "fixtures" / "lecture_v2_valid.json"
    document = json.loads(fixture.read_text(encoding="utf-8"))

    frames_dir = root / "frames"
    rows = []
    names = []
    for index, segment in enumerate(document["segments"]):
        name = "frames/%s-%04d.png" % (stem, index + 1)
        image = synthetic.solid_image(
            root / name, synthetic.SLIDE_COLORS[index % len(synthetic.SLIDE_COLORS)],
            str(index + 1),
        )
        names.append(name)
        rows.append(record_for_file(float(segment["start_sec"]) + 1.0, image, root))
        segment["frame"] = name
        segment["frames"] = [name]
        segment["frame_ocr"] = [{"frame": name, "text": "slide %d" % (index + 1)}]

    recording = _silent_video(root, stem + ".mp4") if video else None
    document["stem"] = stem
    document["source"]["video"] = stem + ".mp4"
    document["source"]["subtitle"]["path"] = stem + ".srt"

    document_path = root / (stem + ".json")
    document_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    manifest = write_manifest(root / (stem + ".frames.json"), rows)
    subtitle = _write_subtitle(
        root / (stem + ".srt"),
        [
            (index * 20.0, index * 20.0 + 19.0, word * 3)
            for index, word in enumerate(_CUE_WORDS)
        ],
    )
    note = root / (stem + ".v4.md")
    note.write_text(
        render_skeleton(document, style="faithful", stem=stem),
        encoding="utf-8", newline="\n",
    )
    return SyntheticLecture(
        root=root, stem=stem, video=recording, subtitle=subtitle,
        document=document_path, manifest=manifest, frames_dir=frames_dir, note=note,
    )


@pytest.fixture()
def synthetic_lecture(tmp_path: Path) -> SyntheticLecture:
    """A clean lecture output set: mp4, srt, json, frames.json, frames/, v4.md."""
    pytest.importorskip("PIL", reason="Pillow paints the synthetic frames")
    return build_synthetic_lecture(tmp_path)
