"""`l2n` entry point: one subcommand per pipeline stage.

Only the cross-cutting contracts are implemented at this point: argument shapes,
the mandatory ``--lang`` gate, the missing-dependency path and the output flags.
Each stage body still reports ``not implemented yet`` and exits 4, so an
unfinished stage can never be mistaken for a successful one.

Exit codes (see ``lecture2notes.exit_codes``):
    0 ok / 1 warning / 2 contract or usage error / 3 missing dependency
    / 4 stage not implemented yet
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import unquote

from lecture2notes import __version__, _deps, _out, exit_codes
from lecture2notes import install as install_mod
from lecture2notes.acceptance import audit, check
from lecture2notes.acceptance.check import check_json
from lecture2notes.engines import calibrate
from lecture2notes.engines import convert
from lecture2notes.engines import corrections as corrections_mod
from lecture2notes.engines import pipeline, registry
from lecture2notes.engines.base import Engine
from lecture2notes.frames import capture as capture_mod
from lecture2notes.frames import curation as curation_mod
from lecture2notes.frames import ocr as ocr_mod
from lecture2notes.frames import scene as scene_mod
from lecture2notes.frames.manifest import read_manifest, write_manifest
from lecture2notes.notes import guideline, render
from lecture2notes.outputs import hub as hub_mod
from lecture2notes.outputs import pbf as pbf_mod
from lecture2notes.outputs import publish as publish_mod
from lecture2notes.outputs import viewer as viewer_mod
from lecture2notes.outputs.plan import plan_for
from lecture2notes.profiles import loader
from lecture2notes.schema import condense as condense_mod
from lecture2notes.schema import scaffold as scaffold_mod
from lecture2notes.schema.io import read_json, write_json_atomic
from lecture2notes.schema.migrate import migrate_file

LANG_CHOICES = ("zh", "en", "ja", "auto")
LANG_REQUIRED_MESSAGE = "--lang is required (zh|en|ja|auto)"

# Order matters: `l2n --help` lists the stages in pipeline order, then the
# maintenance subcommands.
SUBCOMMANDS: List[str] = [
    "transcribe",
    "calibrate-subs",
    "frames",
    "ocr",
    "condense",
    "scaffold",
    "render",
    "viewer",
    "pbf",
    "hub",
    "publish",
    "check",
    "migrate",
    "run",
    "convert-model",
    "profile",
    "install-skill",
]


class StageNotImplemented(Exception):
    """A stage exists and validates its arguments but has no body yet."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(name)


def not_implemented(name: str) -> None:
    raise StageNotImplemented(name)


# --------------------------------------------------------------------------
# shared option groups
# --------------------------------------------------------------------------
def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--quiet", action="store_true", help="關閉進度與提示輸出，只保留警告與錯誤"
    )
    parser.add_argument(
        "--json-progress",
        action="store_true",
        help="進度改以每行一個 JSON 物件輸出（供 agent 追蹤）",
    )
    parser.add_argument(
        "--force", action="store_true", help="忽略既有產物，重跑本階段"
    )
    # The cli layer of the configuration stack. Shared by every subcommand
    # rather than added one at a time, because "which profile am I running
    # under" is a question every stage that reads a template or a setting has
    # to answer the same way. `main` installs it once around the dispatch, so a
    # stage body never has to pass it down.
    parser.add_argument(
        "--profile",
        default=None,
        help="profile 名稱（最高優先層，勝過 overlay 與文件內的設定）",
    )
    return parser


def _add_lang(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--lang",
        choices=list(LANG_CHOICES),
        default=None,
        help="轉錄語言，必填，無預設值（zh|en|ja|auto）",
    )


def require_lang(args: argparse.Namespace) -> str:
    """Enforce the mandatory language gate before any file is created."""
    lang = getattr(args, "lang", None)
    if not lang:
        _out.error(LANG_REQUIRED_MESSAGE)
        raise SystemExit(exit_codes.ERROR)
    return lang


# --------------------------------------------------------------------------
# engine resolution
# --------------------------------------------------------------------------
#: Maps a CLI flag onto the constructor keyword an engine would call it.
ENGINE_OPTION_FLAGS = {
    "model": "model",
    "model_dir": "model_dir",
    "aligner_dir": "aligner_dir",
    "backend": "backend",
    "whisper_cpp_bin": "binary",
    "whisper_cpp_model": "model",
}


def engine_options(args: argparse.Namespace, factory) -> Dict[str, Any]:
    """Which of the engine flags this particular backend actually accepts.

    Flags are filtered against the constructor rather than hard-coded per engine
    name, so a third-party engine gets the same treatment as a shipped one.
    """
    try:
        accepted = set(inspect.signature(factory).parameters)
    except (TypeError, ValueError):  # pragma: no cover - exotic callables
        accepted = set(ENGINE_OPTION_FLAGS.values())
    options: Dict[str, Any] = {}
    for flag, keyword in ENGINE_OPTION_FLAGS.items():
        value = getattr(args, flag, None)
        if value in (None, ""):
            continue
        if keyword in accepted:
            options[keyword] = value
    return options


def resolve_engine(args: argparse.Namespace) -> Engine:
    """Build the requested engine, refusing a cloud one without --allow-cloud."""
    name = getattr(args, "engine", None) or registry.DEFAULT_ENGINE
    try:
        factory = registry._get(name)
    except KeyError as exc:
        _out.error(str(exc).strip("'"))
        raise SystemExit(exit_codes.ERROR)
    options = engine_options(args, factory)
    # whisper.cpp names its ggml file with --whisper-cpp-model, so a bare
    # --model must not silently become the path to a model file.
    if name == "whisper_cpp" and getattr(args, "whisper_cpp_model", None):
        options["model"] = args.whisper_cpp_model
    try:
        return registry.create(
            name, allow_cloud=getattr(args, "allow_cloud", False), **options
        )
    except registry.CloudEngineBlocked as exc:
        _out.error(str(exc))
        raise SystemExit(exit_codes.ERROR)


def load_corrections(args: argparse.Namespace):
    """Read the correction table, or return None when none was asked for."""
    table = getattr(args, "corrections", None)
    if not table:
        return None, None
    path = Path(table)
    if not path.is_file():
        _out.error("corrections table not found: %s" % path)
        raise SystemExit(exit_codes.ERROR)
    return corrections_mod.load_table_file(path), str(path)


# --------------------------------------------------------------------------
# stage handlers
# --------------------------------------------------------------------------
def cmd_transcribe(args: argparse.Namespace) -> int:
    if getattr(args, "list_engines", False):
        for text in registry.engine_lines():
            _out.line(text)
        return exit_codes.OK
    lang = require_lang(args)
    video = getattr(args, "video", None)
    if not video:
        _out.error("transcribe needs a video or audio file")
        return exit_codes.ERROR
    source = Path(video)
    if not source.is_file():
        _out.error("no such file: %s" % source)
        return exit_codes.ERROR
    _deps.require("ffmpeg")
    engine = resolve_engine(args)
    pairs, table_path = load_corrections(args)
    result = pipeline.transcribe_video(
        source,
        lang,
        engine,
        pairs=pairs,
        table_path=table_path,
        s2t=not getattr(args, "no_s2t", False),
        force=getattr(args, "force", False),
        # An engine that cannot take an arbitrarily long input says so itself;
        # --chunk-sec overrides that, and no engine forces chunking on the rest.
        chunk_sec=getattr(args, "chunk_sec", None) or getattr(engine, "chunk_sec", None),
    )
    if result.skipped:
        return exit_codes.OK
    _out.ok("transcribe: %s" % ", ".join(p.name for p in result.outputs()))
    return exit_codes.OK


def cmd_calibrate_subs(args: argparse.Namespace) -> int:
    lang = require_lang(args)
    video = getattr(args, "video", None)
    subs = getattr(args, "subs", None)
    if not video or not subs:
        _out.error("calibrate-subs needs a video and a subtitle file")
        return exit_codes.ERROR
    for path in (Path(video), Path(subs)):
        if not path.is_file():
            _out.error("no such file: %s" % path)
            return exit_codes.ERROR
    _deps.require("ffmpeg")
    engine = resolve_engine(args)
    engine.check()
    with tempfile.TemporaryDirectory(prefix="l2n-probes-") as scratch:
        try:
            result = calibrate.run_calibration(
                Path(video),
                Path(subs),
                lang,
                engine,
                probes=getattr(args, "probes", None),
                out_dir=Path(args.out_dir) if getattr(args, "out_dir", None) else None,
                workdir=Path(scratch),
            )
        except calibrate.CalibrationError as exc:
            _out.error(str(exc))
            return exit_codes.ERROR
    _out.ok("calibrate-subs: %s" % ", ".join(
        path.name for path in result["files"].values()
    ))
    return exit_codes.OK


def _curate_frames(video: Path, args: argparse.Namespace) -> int:
    """`l2n frames <video> --curate`: promote staged candidates into frames/.

    The staging manifest is the input and the same path is the output: after
    promotion ``<stem>.frames.json`` lists the formal frames only, which is what
    makes "the canonical JSON references only promoted frames" true by
    construction rather than by convention.
    """
    base_dir = video.parent
    stem = scene_mod.video_stem(video)
    manifest_path = capture_mod.manifest_path_for(base_dir, stem)
    if not manifest_path.is_file():
        _out.error("no staged manifest to curate: %s" % manifest_path.name)
        return exit_codes.ERROR

    document_path = capture_mod.document_path_for(base_dir, stem)
    document = read_json(document_path) if document_path.is_file() else None
    result = curation_mod.curate(
        read_manifest(manifest_path),
        base_dir,
        document=document,
        max_per_segment=getattr(args, "max_per_segment", curation_mod.DEFAULT_MAX_PER_SEGMENT),
    )

    write_json_atomic(capture_mod.curation_path_for(base_dir, stem), result.report())
    write_manifest(manifest_path, result.promoted)
    if document is not None and result.promoted:
        capture_mod.merge_into_document(document_path, result.promoted)

    for text in result.lines():
        _out.line(text)
    _out.stage(
        "frames",
        "curate: %d promoted, %d rejected" % (len(result.promoted), len(result.rejected)),
    )
    return result.exit_code()


def cmd_frames(args: argparse.Namespace) -> int:
    _deps.require("ffmpeg")
    _deps.require("ffprobe")
    video = getattr(args, "video", None)
    if not video:
        _out.error("frames needs a video file")
        return exit_codes.ERROR
    source = Path(video)
    if not source.is_file():
        _out.error("no such file: %s" % source)
        return exit_codes.ERROR

    if getattr(args, "curate", False):
        return _curate_frames(source, args)

    try:
        result = capture_mod.capture(
            source,
            mode=getattr(args, "mode", "scene"),
            every=getattr(args, "every", 45.0),
            diff_min=getattr(args, "diff_min", 4.0),
            width=getattr(args, "width", scene_mod.DEFAULT_WIDTH),
            stage=getattr(args, "stage", False),
        )
    except capture_mod.DurationUnknown as exc:
        # Nothing was written: an unknown duration is a usage-level error, not
        # a run that happened to find no frames.
        _out.error("frames: %s" % exc)
        return exit_codes.ERROR
    if not result.kept:
        _out.error("frames: capture produced no frames")
        return exit_codes.ERROR
    _out.ok("frames: %s" % result.manifest_path.name)
    return exit_codes.OK


def cmd_ocr(args: argparse.Namespace) -> int:
    # Checked before the target is even resolved: a run that cannot OCR must say
    # so with exit 3 rather than reporting a missing file it never needed.
    _deps.require("rapidocr")
    target = getattr(args, "target", None)
    if not target:
        _out.error("ocr needs a frames folder or a canonical JSON")
        return exit_codes.ERROR
    path = Path(target)
    if not path.exists():
        _out.error("no such file or folder: %s" % path)
        return exit_codes.ERROR
    try:
        result = ocr_mod.run_ocr(
            path,
            min_conf=getattr(args, "min_conf", ocr_mod.DEFAULT_MIN_CONF),
            s2t=not getattr(args, "no_s2t", False),
            force=getattr(args, "force", False),
        )
    except ocr_mod.OcrTargetError as exc:
        # A frames folder that names no lecture, or more than one. Writing an
        # orphan cache nobody reads is the failure this replaces.
        _out.error("ocr: %s" % exc.message)
        return exit_codes.ERROR
    _out.ok(
        "ocr: %d frames, %d with text" % (result.total, len(result.recognised))
    )
    if result.document is not None:
        _out.stage("ocr", "merged into %s" % result.document.name)
    return exit_codes.OK


#: What `l2n scaffold` prints after writing, because the file it just wrote is
#: not the deliverable. Every semantic field in it is a placeholder.
SCAFFOLD_NEXT = (
    "every field marked %s is yours to write:段落邊界、標題、摘要、條列。"
    "寫完後刪掉頂層的 \"draft\"，再跑 l2n check json"
) % scaffold_mod.AI_DRAFT_MARK


def cmd_condense(args: argparse.Namespace) -> int:
    """`l2n condense <srt>`: one line per minute, for reading in one pass.

    Deciding where a three-hour lecture's segments begin means reading the
    whole transcript, and ninety percent of an SRT is timecode. This has been
    in the package since the port; it had no way to be run.
    """
    target = getattr(args, "subtitle", None)
    if not target:
        _out.error("condense needs a subtitle file")
        return exit_codes.ERROR
    path = Path(target)
    if not path.is_file():
        _out.error("no such file: %s" % path)
        return exit_codes.ERROR
    window = int(getattr(args, "window", condense_mod.DEFAULT_BUCKET_SEC) or 0)
    if window <= 0:
        _out.error("--window must be a positive number of seconds")
        return exit_codes.ERROR

    out = getattr(args, "out", None)
    destination = Path(out) if out else path.with_name(
        path.stem + scaffold_mod.CONDENSED_SUFFIX
    )
    written = condense_mod.condense_file(path, destination, window)
    counts = condense_mod.stats(
        condense_mod.read_subtitle_text(path), window
    )
    _out.ok("condense -> %s (%d lines, %d chars)" % (
        written, counts["buckets"], counts["chars"]
    ))
    return exit_codes.OK


def cmd_scaffold(args: argparse.Namespace) -> int:
    """`l2n scaffold <srt>`: the v2 shape, with every meaning left blank.

    The shape is mechanical: equal-time segments, real timecodes, the captured
    frames merged in, `"draft": true` on top. The meaning is not, and this
    command does not pretend otherwise -- it writes `<!-- ai-draft -->` into
    every semantic field and says so on the way out.
    """
    target = getattr(args, "target", None)
    if not target:
        _out.error("scaffold needs a subtitle file")
        return exit_codes.ERROR
    path = Path(target)
    if not path.is_file():
        _out.error("no such file: %s" % path)
        return exit_codes.ERROR
    segments = int(getattr(args, "segments", scaffold_mod.DEFAULT_SEGMENTS) or 0)
    if segments < 1:
        _out.error("--segments must be at least 1")
        return exit_codes.ERROR
    window = int(getattr(args, "window", condense_mod.DEFAULT_BUCKET_SEC) or 0)
    if window <= 0:
        _out.error("--window must be a positive number of seconds")
        return exit_codes.ERROR

    document = path.with_name(path.stem + ".json")
    if document.exists() and not getattr(args, "force", False):
        # Overwriting a document a model has already written is the one
        # unrecoverable mistake this stage can make.
        _out.stage("scaffold", "skip (%s exists; use --force)" % document.name)
        return exit_codes.OK
    try:
        result = scaffold_mod.scaffold_file(
            path, segments=segments, bucket_sec=window
        )
    except ValueError as exc:
        _out.error("scaffold: %s" % exc)
        return exit_codes.ERROR
    _out.ok("scaffold -> %s (%d segments, %d frames)" % (
        result.document_path, result.segments, result.frames
    ))
    _out.stage("scaffold", "condensed -> %s" % result.condensed_path.name)
    _out.stage("scaffold", SCAFFOLD_NEXT)
    return exit_codes.OK


def cmd_render(args: argparse.Namespace) -> int:
    """`l2n render <doc>`: the deterministic skeleton, or the expansion bundle.

    The skeleton is the file the language model then rewrites in place, so an
    existing note is never overwritten without ``--force``: losing an expanded
    note to a re-run of a mechanical stage is not a recoverable mistake.
    """
    path, data = _load_document(getattr(args, "json_file", None), "render")
    if path is None:
        return exit_codes.ERROR

    # The document names a profile, but it is only one layer: `--profile` and an
    # overlay both outrank it, and both already decide which template and which
    # settings this render actually uses. Resolving here means the line the
    # command prints names the same profile the output was built from -- it used
    # to print the document's field and so reported "generic" for a note
    # rendered from the radiology template.
    profile = loader.resolve(str(data.get("profile") or loader.BUILTIN_PROFILE)).profile
    if not (loader.profile_dir(profile) / loader.NOTE_TEMPLATE).is_file():
        profile = loader.BUILTIN_PROFILE
    style = loader.note_style(profile, getattr(args, "style", None))
    note_path = path.with_name(path.stem + ".v4.md")

    if getattr(args, "expand_prompt", False):
        _out.line(guideline.expand_prompt(path, note_path, style=style))
        return exit_codes.OK

    if note_path.exists() and not getattr(args, "force", False):
        _out.stage("render", "skip (%s exists; use --force)" % note_path.name)
        return exit_codes.OK
    render.write_skeleton(note_path, data, style=style, stem=path.stem, profile=profile)
    _out.ok("render -> %s (style %s, profile %s)" % (note_path, style, profile))
    return exit_codes.OK


def _emit_plan(stage: str, planned) -> int:
    """Print what a real run would write, then stop. Writes nothing itself."""
    for item in planned:
        _out.line("%s %s" % (stage, item.line()))
    if not planned:
        _out.line("%s nothing to write" % stage)
    return exit_codes.OK


def _load_document(target: Optional[str], what: str):
    """Read a canonical JSON, reporting the usage or parse error itself."""
    path = _existing_path(target, what)
    if path is None:
        return None, None
    try:
        data = read_json(path)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _out.error("%s：JSON 無法解析 %s（%s）" % (what, path.name, exc))
        return None, None
    if not isinstance(data, dict):
        _out.error("%s：JSON 最外層必須是物件 %s" % (what, path.name))
        return None, None
    return path, data


def cmd_viewer(args: argparse.Namespace) -> int:
    path, data = _load_document(getattr(args, "json_file", None), "viewer")
    if path is None:
        return exit_codes.ERROR
    if getattr(args, "preflight", False):
        return _emit_plan("viewer", viewer_mod.preflight(path))
    if not (data.get("segments") or []):
        _out.error("viewer：JSON 沒有 segments，無法產生頁面")
        return exit_codes.ERROR
    destination = viewer_mod.viewer_path(path)
    if destination.exists() and not getattr(args, "force", False):
        _out.skip("viewer: %s 已存在（--force 重做）" % destination.name)
        return exit_codes.OK
    result = viewer_mod.build(path, data)
    if not result["video"]:
        _out.warn("viewer：找不到影片檔，頁面的播放器沒有來源")
    _out.ok(
        "viewer: %s (%d segments, %d blocks, %d estimated times, %d cues)"
        % (
            result["path"].name, result["segments"], result["blocks"],
            result["estimated"], result["cues"],
        )
    )
    return exit_codes.OK


def cmd_pbf(args: argparse.Namespace) -> int:
    path, data = _load_document(getattr(args, "json_file", None), "pbf")
    if path is None:
        return exit_codes.ERROR
    segments = data.get("segments") or []
    if not segments:
        _out.error("pbf：JSON 沒有 segments，無法產生章節")
        return exit_codes.ERROR
    stem, note = pbf_mod.match_video_stem(path)
    destination = path.parent / (stem + ".pbf")
    if destination.exists() and not getattr(args, "force", False):
        _out.skip("pbf: %s 已存在（--force 重做）" % destination.name)
        return exit_codes.OK
    written = pbf_mod.write_pbf(path, data, out=destination)
    _out.ok(
        "pbf: %s (%d chapters, %s)"
        % (written.name, pbf_mod.chapter_count(data), note)
    )
    return exit_codes.OK


def cmd_hub(args: argparse.Namespace) -> int:
    folder = getattr(args, "folder", None)
    if not folder:
        _out.error("hub 需要一個課程資料夾路徑")
        return exit_codes.ERROR
    root = Path(folder)
    if not root.is_dir():
        _out.error("hub：不是資料夾 %s" % root)
        return exit_codes.ERROR
    if getattr(args, "preflight", False):
        return _emit_plan("hub", hub_mod.preflight(root))
    result = hub_mod.build(root, title=getattr(args, "title", None))
    if not result["cards"]:
        _out.error("hub：資料夾裡沒有可用的正式 JSON")
        return exit_codes.ERROR
    _out.ok(
        "hub: %s (%d lectures, %d index rows)"
        % (result["path"].name, len(result["cards"]), result["index_rows"])
    )
    # Checked after the write, against the links the page actually carries. A
    # hub full of dead links is worse than no hub: it looks like the lecture is
    # there and it is not, and nothing else in the pipeline would notice.
    for href in result["missing"]:
        _out.error("hub：連結指向不存在的檔案 %s" % unquote(href))
    if result["missing"]:
        return exit_codes.ERROR
    return exit_codes.OK


def cmd_publish(args: argparse.Namespace) -> int:
    """`l2n publish <stem> --dest <dir>`: the derivative set, as one transaction.

    Publishing means replacing several files at once. One at a time means a
    failure halfway leaves the destination holding a new viewer, an old chapter
    file and a note from neither: the pieces disagree and nothing records that
    they do. So either all of them land or none of them do.
    """
    stem_arg = getattr(args, "stem", None)
    destination = getattr(args, "dest", None)
    if not stem_arg:
        _out.error("publish needs a lecture stem")
        return exit_codes.ERROR
    if not destination:
        _out.error("publish needs --dest <dir>")
        return exit_codes.ERROR

    base = check.stem_path(Path(stem_arg))
    source_dir = base.parent if str(base.parent) else Path(".")
    document = base.with_name(base.name + ".json")
    if not document.is_file():
        _out.error("publish：找不到正式 JSON %s" % document)
        return exit_codes.ERROR

    profile = _run_profile(document)
    outcome = publish_mod.publish_lecture(
        base.name,
        Path(destination),
        source_dir,
        pbf_enabled=pbf_mod.is_enabled(loader.outputs(profile)),
    )
    if not outcome.ok:
        if outcome.failed_path:
            _out.error("publish failed on %s" % outcome.failed_path)
        _out.error(outcome.message)
        if outcome.backup_dir:
            _out.warn(
                "rollback did not finish; the backups are in %s" % outcome.backup_dir
            )
        return exit_codes.ERROR

    for name in outcome.files:
        _out.line("publish -> %s" % name)
    _out.ok(outcome.message)
    _out.line("publish backup_dir: %s" % (outcome.backup_dir or "none"))
    return exit_codes.OK


#: Stages `l2n check` knows about. Only the ones with a handler are wired up;
#: the rest still report "not implemented yet" rather than silently passing.
CHECK_STAGES = ("transcribe", "frames", "json", "note")


def _existing_path(value: Optional[str], what: str) -> Optional[Path]:
    """Resolve a required path argument, reporting the usage error itself."""
    if not value:
        _out.error("%s：缺少路徑參數" % what)
        return None
    path = Path(value)
    if not path.exists():
        _out.error("%s：找不到檔案 %s" % (what, path))
        return None
    return path


def _check_transcribe(target: Optional[str]) -> int:
    """`l2n check transcribe <srt>`: cue structure and hallucination loops."""
    if not target:
        _out.error("check transcribe needs a subtitle file")
        return exit_codes.ERROR
    path = Path(target)
    if not path.is_file():
        _out.error("no such file: %s" % path)
        return exit_codes.ERROR

    report = check.check_transcribe_stage(path)
    report.emit()
    return report.exit_code()


def _check_json(target: Optional[str]) -> int:
    """`l2n check json <doc>`: the canonical schema v2 validator."""
    path = _existing_path(target, "check json")
    if path is None:
        return exit_codes.ERROR
    report = check_json(path)
    report.emit()
    return report.exit_code()


def _check_frames(target: Optional[str]) -> int:
    """`l2n check frames <stem>.frames.json`: files exist, hashes match."""
    path = _existing_path(target, "check frames")
    if path is None:
        return exit_codes.ERROR
    report = check.check_frames(path)
    report.emit()
    return report.exit_code()


def _check_note(args: argparse.Namespace) -> int:
    """`l2n check note <doc> --note <note>`: rules R1 to R8 of the guideline.

    The version line comes first so a saved report always says which edition of
    the rules produced it; a report that does not is not evidence of anything.
    """
    path = _existing_path(getattr(args, "target", None), "check note")
    if path is None:
        return exit_codes.ERROR
    note = Path(args.note) if getattr(args, "note", None) else path.with_name(
        path.stem + ".v4.md"
    )
    report = check.check_note_stage(
        path, note, style=getattr(args, "style", None)
    )
    _out.line(guideline.VERSION_LINE)
    report.emit()
    return report.exit_code()


def _check_all(args: argparse.Namespace) -> int:
    """`l2n check --all <stem> [--report <path>]`: every applicable stage.

    "Applicable" is what is on disk. A lecture whose note has not been written
    is not failing the note stage, it has not reached it, and an error for a
    file the pipeline was never asked to produce would make this useless as a
    progress check. A stem with nothing at all beside it is a different matter:
    that is a mistyped path, and it exits 2 rather than reporting a clean run
    over zero stages.
    """
    stem = getattr(args, "what", None) or getattr(args, "target", None)
    if not stem:
        _out.error("check --all needs a stem")
        return exit_codes.ERROR
    base = check.stem_path(Path(stem))
    reports = check.check_all(base, style=getattr(args, "style", None))
    if not reports:
        _out.error("check --all：找不到任何階段產物 %s" % base.name)
        return exit_codes.ERROR

    _out.line(guideline.VERSION_LINE)
    for report in reports:
        report.emit()

    report_path = getattr(args, "report", None)
    if report_path:
        payload = audit.stage_report_payload(
            reports, stem=base.name, guideline_version=guideline.GUIDELINE_VERSION
        )
        written = audit.write_stage_report(Path(report_path), payload)
        _out.ok("check --all -> %s" % written)
    return check.worst_exit_code(reports)


def cmd_check(args: argparse.Namespace) -> int:
    if getattr(args, "all_stages", False):
        return _check_all(args)
    stage = getattr(args, "what", None)
    if not stage:
        _out.error("check needs a stage: %s" % " / ".join(CHECK_STAGES))
        return exit_codes.ERROR
    if stage not in CHECK_STAGES:
        _out.error("unknown check stage: %s (known: %s)"
                   % (stage, ", ".join(CHECK_STAGES)))
        return exit_codes.ERROR
    target = getattr(args, "target", None)
    if stage == "transcribe":
        return _check_transcribe(target)
    if stage == "frames":
        return _check_frames(target)
    if stage == "json":
        return _check_json(target)
    if stage == "note":
        return _check_note(args)
    not_implemented("check %s" % stage)
    return exit_codes.OK


def cmd_migrate(args: argparse.Namespace) -> int:
    path = _existing_path(args.json_file, "migrate")
    if path is None:
        return exit_codes.ERROR
    try:
        result = migrate_file(path)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _out.error("migrate：JSON 無法解析 %s（%s）" % (path.name, exc))
        return exit_codes.ERROR
    if result.changed:
        _out.ok(result.summary)
    else:
        _out.skip(result.summary)
    return exit_codes.OK


class RunPaths:
    """Every file `l2n run` can write for one recording, resolved once.

    Resolved before any stage starts so ``--preflight`` and the run itself
    answer from the same list: a preflight that computed its targets separately
    would be a second implementation of the thing it is supposed to predict.
    """

    def __init__(self, video: Path, profile: str = loader.BUILTIN_PROFILE) -> None:
        self.video = Path(video)
        self.base = self.video.parent
        self.stem = scene_mod.video_stem(self.video)
        self.subtitle = self.base / (self.stem + ".srt")
        self.manifest = self.base / (self.stem + ".frames.json")
        self.frames_dir = self.base / "frames"
        self.document = self.base / (self.stem + ".json")
        self.ocr_cache = ocr_mod.cache_path_for(self.document)
        self.note = self.base / (self.stem + ".v4.md")
        self.viewer = viewer_mod.viewer_path(self.document)
        self.pbf = self.base / (self.stem + ".pbf")
        self.profile = profile

    def pbf_enabled(self) -> bool:
        return pbf_mod.is_enabled(loader.outputs(self.profile))

    def planned(self) -> List[Path]:
        """The write targets, in the order the stages reach them."""
        targets = [
            self.subtitle, self.manifest, self.ocr_cache,
            self.document, self.note, self.viewer,
        ]
        if self.pbf_enabled():
            targets.append(self.pbf)
        return targets


def _run_profile(paths_document: Path) -> str:
    """The profile the document declares, or the built-in one."""
    if not paths_document.is_file():
        return loader.BUILTIN_PROFILE
    try:
        data = read_json(paths_document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return loader.BUILTIN_PROFILE
    name = data.get("profile") if isinstance(data, dict) else None
    if isinstance(name, str) and name and loader.profile_dir(name).is_dir():
        return name
    return loader.BUILTIN_PROFILE


#: What a stage prints when its output is already there.
SKIP_EXISTS = "skip (exists)"

#: scaffold is not a mechanical stage. It is the one place a language model has
#: to write, so `run` states that plainly instead of pretending to do it.
SCAFFOLD_SKIP = "skip (LLM stage; see skill)"

#: What to do when the model has not written the document yet. Naming the skill
#: matters: the next step is not something the CLI can perform.
SCAFFOLD_MISSING = (
    "%s does not exist; run `l2n scaffold <stem>.srt` for the shape, then the "
    "lecture2notes skill to write the semantics, then run again"
)


class _StageRunner:
    """Runs the stages in order and remembers the worst outcome so far.

    `run` has two failure modes that must not be confused. A check error means
    the artefact is wrong, so nothing downstream may be built from it and the
    run stops. A check warning means the artefact is usable and somebody should
    look at it, so the run continues and says so at the end with exit 1. That
    distinction is the whole point of the acceptance contract, so it lives in
    one place rather than being re-decided at each call site.
    """

    def __init__(self) -> None:
        self.worst = exit_codes.OK

    def failed(self, stage: str, reason: str) -> int:
        _out.stage(stage, "error: %s" % reason)
        self.worst = exit_codes.ERROR
        return exit_codes.ERROR

    def accept(self, stage: str, report) -> bool:
        """Emit a stage check and say whether the run may continue.

        The findings and the summary are printed in the acceptance contract's
        own format, but the ``[stage] ok`` line uses the *run* stage's name
        rather than the check's. They differ twice: scaffold is accepted by
        `check json` and render by `check note`, and a run that reported
        ``[json] ok`` in the middle of the scaffold stage would be describing a
        stage the user never asked for.
        """
        for text in report.lines():
            _out.line(text)
        _out.line(report.summary_line())
        code = report.exit_code()
        if code == exit_codes.ERROR:
            first = report.errors[0]
            self.failed(stage, "%s %s: %s" % (first.code, first.location or "-",
                                              first.message))
            return False
        if code == exit_codes.WARN:
            self.worst = max(self.worst, exit_codes.WARN)
        _out.stage(stage, "ok")
        return True

    def done(self, stage: str) -> bool:
        """A stage that has no acceptance check of its own."""
        _out.stage(stage, "ok")
        return True


def _run_transcribe(args, paths: RunPaths, lang: str, force: bool) -> Optional[str]:
    """Do the transcribe stage, or report why it could not. None means fine."""
    if paths.subtitle.is_file() and not force:
        _out.stage("transcribe", SKIP_EXISTS)
        return None
    engine = resolve_engine(args)
    pairs, table_path = load_corrections(args)
    try:
        pipeline.transcribe_video(
            paths.video, lang, engine,
            pairs=pairs, table_path=table_path, force=force,
            chunk_sec=getattr(args, "chunk_sec", None)
            or getattr(engine, "chunk_sec", None),
        )
    except RuntimeError as exc:
        return str(exc)
    return None


def _run_frames(args, paths: RunPaths, force: bool) -> Optional[str]:
    if paths.manifest.is_file() and not force:
        _out.stage("frames", SKIP_EXISTS)
        return None
    try:
        result = capture_mod.capture(
            paths.video,
            mode=getattr(args, "mode", None) or "scene",
            every=getattr(args, "every", None) or 45.0,
        )
    except (capture_mod.DurationUnknown, ValueError, FileNotFoundError) as exc:
        return str(exc)
    if not result.kept:
        return "capture produced no frames"
    return None


def _run_ocr(paths: RunPaths, force: bool) -> Optional[str]:
    """OCR the captured frames, unless the cache already covers them.

    The dependency is required only when there is work to do. Asking for
    rapidocr in order to discover there is nothing to recognise would make a
    resumed run fail on a machine where the first run succeeded.
    """
    if paths.ocr_cache.is_file() and not force:
        _out.stage("ocr", SKIP_EXISTS)
        return None
    _deps.require("rapidocr")
    target = paths.document if paths.document.is_file() else paths.frames_dir
    if not target.exists():
        return "no frames to read: %s" % paths.frames_dir
    try:
        ocr_mod.run_ocr(target, force=force)
    except (OSError, RuntimeError) as exc:
        return str(exc)
    return None


def _run_render(paths: RunPaths, style: Optional[str], force: bool) -> Optional[str]:
    if paths.note.is_file() and not force:
        _out.stage("render", SKIP_EXISTS)
        return None
    try:
        data = read_json(paths.document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return "cannot read %s: %s" % (paths.document.name, exc)
    profile = paths.profile
    if not (loader.profile_dir(profile) / loader.NOTE_TEMPLATE).is_file():
        profile = loader.BUILTIN_PROFILE
    render.write_skeleton(
        paths.note, data,
        style=loader.note_style(profile, style),
        stem=paths.stem, profile=profile,
    )
    return None


def _run_viewer(paths: RunPaths, force: bool) -> Optional[str]:
    if paths.viewer.is_file() and not force:
        _out.stage("viewer", SKIP_EXISTS)
        return None
    try:
        data = read_json(paths.document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return "cannot read %s: %s" % (paths.document.name, exc)
    if not (data.get("segments") or []):
        return "%s has no segments" % paths.document.name
    viewer_mod.build(paths.document, data)
    return None


def _run_pbf(paths: RunPaths, force: bool) -> Optional[str]:
    if paths.pbf.is_file() and not force:
        _out.stage("pbf", SKIP_EXISTS)
        return None
    try:
        data = read_json(paths.document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return "cannot read %s: %s" % (paths.document.name, exc)
    pbf_mod.write_pbf(paths.document, data, out=paths.pbf)
    return None


def cmd_run(args: argparse.Namespace) -> int:
    """`l2n run <video> --lang xx`: every mechanical stage, with the checks.

    The order is transcribe, frames, ocr, scaffold, render, viewer, and the
    chapter file when the profile enables it. After each stage that has one, its
    acceptance check runs: an error stops the run so that nothing downstream is
    built from a broken artefact, a warning does not.

    Re-running is the normal case, not the exception -- a thirty-minute lecture
    is transcribed once and then rendered and viewed many times -- so every
    stage skips when its output is there. ``--force`` is how you say you meant
    it, and it is per-run rather than per-stage on purpose: a flag that redid
    only the stage you named would still leave the later ones stale.
    """
    # The language gate comes before everything, including --preflight: it is
    # the one contract that must fire before any path is even resolved.
    lang = require_lang(args)
    video = getattr(args, "video", None)
    if not video:
        _out.error("run needs a video file")
        return exit_codes.ERROR
    source = Path(video)
    if not source.is_file():
        _out.error("no such file: %s" % source)
        return exit_codes.ERROR
    paths = RunPaths(source, _run_profile(Path(source).with_suffix(".json")))
    if getattr(args, "preflight", False):
        # No dependency check: listing what a run would write must work on a
        # machine that cannot run it, which is exactly when it is most useful.
        return _emit_plan("run", plan_for(paths.planned()))

    _deps.require("ffmpeg")
    force = bool(getattr(args, "force", False))
    style = getattr(args, "style", None)
    runner = _StageRunner()

    reason = _run_transcribe(args, paths, lang, force)
    if reason is not None:
        return runner.failed("transcribe", reason)
    if not runner.accept("transcribe", check.check_transcribe_stage(paths.subtitle)):
        return runner.worst

    reason = _run_frames(args, paths, force)
    if reason is not None:
        return runner.failed("frames", reason)
    if not runner.accept("frames", check.check_frames(paths.manifest)):
        return runner.worst

    reason = _run_ocr(paths, force)
    if reason is not None:
        return runner.failed("ocr", reason)
    runner.done("ocr")

    _out.stage("scaffold", SCAFFOLD_SKIP)
    if not paths.document.is_file():
        return runner.failed("scaffold", SCAFFOLD_MISSING % paths.document.name)
    if not runner.accept("scaffold", check.check_json(paths.document)):
        return runner.worst

    reason = _run_render(paths, style, force)
    if reason is not None:
        return runner.failed("render", reason)
    if not runner.accept("render", check.check_note_stage(
        paths.document, paths.note,
        transcript=paths.subtitle if paths.subtitle.is_file() else None,
        style=style, profile=paths.profile,
    )):
        return runner.worst

    reason = _run_viewer(paths, force)
    if reason is not None:
        return runner.failed("viewer", reason)
    runner.done("viewer")

    if paths.pbf_enabled():
        reason = _run_pbf(paths, force)
        if reason is not None:
            return runner.failed("pbf", reason)
        runner.done("pbf")

    _out.ok("run: %s" % ("完成，但有警告" if runner.worst else "完成"))
    return runner.worst


def cmd_convert_model(args: argparse.Namespace) -> int:
    try:
        convert.convert(
            out_dir=Path(args.out) if getattr(args, "out", None) else None,
            source=getattr(args, "src", None) or convert.SOURCE_MODEL,
            quantization=getattr(args, "quantization", convert.DEFAULT_QUANTIZATION),
            force=getattr(args, "force", False),
        )
    except convert.ConversionRefused as exc:
        _out.error(str(exc))
        return exit_codes.ERROR
    return exit_codes.OK


PROFILE_ACTIONS = ("show",)


def _profile_value(value: Any) -> str:
    """One setting's value as a single console line.

    ``json.dumps`` rather than ``repr`` so ``true``/``false`` read as the TOML
    the user wrote, and ASCII-escaped so the line survives a cp950 console
    without the escape hatch in ``_out`` having to replace characters.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, Path):
        return str(value)
    return json.dumps(value)


def cmd_profile(args: argparse.Namespace) -> int:
    """`l2n profile show`: every effective key, its value and its source layer."""
    action = getattr(args, "action", None) or "show"
    if action not in PROFILE_ACTIONS:
        _out.error(
            "unknown profile action: %s (known: %s)"
            % (action, " / ".join(PROFILE_ACTIONS))
        )
        return exit_codes.ERROR

    resolved = loader.resolve(overrides={"note.style": getattr(args, "style", None)})

    if getattr(args, "as_json", False):
        # ASCII-escaped on purpose: a correction key is often Traditional
        # Chinese, and a cp950 console would otherwise turn the payload into
        # JSON that no longer parses.
        _out.line(json.dumps(resolved.as_json(), indent=2))
        return exit_codes.OK

    _out.stage("profile", "%s (layers: %s)" % (
        resolved.profile,
        ", ".join(name for name, _ in resolved.layers),
    ))
    for key, setting in resolved.settings().items():
        _out.line("%s = %s  (%s)" % (key, _profile_value(setting.value), setting.source))
    return exit_codes.OK


def cmd_install_skill(args: argparse.Namespace) -> int:
    try:
        if args.check:
            lines, all_ok = install_mod.check(
                target=args.target, all_targets=args.all, dest=args.dest
            )
            for text in lines:
                _out.line(text)
            return exit_codes.OK if all_ok else exit_codes.ERROR

        results = install_mod.install(
            target=args.target, all_targets=args.all, dest=args.dest
        )
    except install_mod.InstallError as exc:
        _out.error(str(exc))
        return exit_codes.ERROR

    for _label, path, skipped in results:
        _out.line(str(path))
        for name in skipped:
            _out.stage("install-skill", "kept existing overlay file: %s" % name)
    _out.ok(
        "installed skill %r to %d target(s)" % (install_mod.SKILL_NAME, len(results))
    )
    return exit_codes.OK


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="l2n",
        description="lecture2notes: 講座影片轉成筆記、同步 viewer 與課程首頁",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="旗標請放在子命令之後，例如：l2n frames video.mp4 --quiet",
    )
    parser.add_argument(
        "--version", action="version", version="lecture2notes %s" % __version__
    )
    sub = parser.add_subparsers(dest="command", metavar="<subcommand>")

    p = sub.add_parser(
        "transcribe", parents=[common], help="以本機 ASR 引擎轉錄影片為 SRT"
    )
    p.add_argument("video", nargs="?", help="影片或音檔路徑")
    _add_lang(p)
    p.add_argument("--engine", default=None, help="轉錄引擎（預設 breeze_ct2）")
    p.add_argument("--model", default=None, help="模型名稱或大小")
    p.add_argument("--model-dir", default=None, help="本機權重目錄")
    p.add_argument(
        "--aligner-dir",
        default=None,
        dest="aligner_dir",
        help="qwen3_asr 的 forced aligner 權重目錄（時間戳由它產生）",
    )
    p.add_argument(
        "--backend",
        default=None,
        help="引擎後端（qwen3_asr：transformers 預設，vllm 選配）",
    )
    p.add_argument(
        "--allow-cloud", action="store_true", help="允許使用非本機引擎（預設拒絕）"
    )
    p.add_argument(
        "--whisper-cpp-bin", default=None, help="whisper.cpp 執行檔路徑"
    )
    p.add_argument(
        "--whisper-cpp-model", default=None, help="whisper.cpp 的 ggml 模型檔路徑"
    )
    p.add_argument(
        "--corrections", default=None, help="對照表 JSON 路徑（套用後寫 raw 與 sidecar）"
    )
    p.add_argument(
        "--no-s2t", action="store_true", help="不做簡轉繁（預設會轉）"
    )
    p.add_argument(
        "--chunk-sec",
        type=float,
        default=None,
        dest="chunk_sec",
        help="把音訊切成這麼長的區塊逐段轉錄（給無法吃長音訊的後端）",
    )
    p.add_argument(
        "--list-engines",
        action="store_true",
        help="列出所有已註冊引擎與其相依狀態後結束",
    )
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser(
        "calibrate-subs", parents=[common], help="量測並線性校正官方字幕的時間偏移"
    )
    p.add_argument("video", nargs="?", help="影片路徑")
    p.add_argument("subs", nargs="?", help="官方字幕路徑（VTT 或 SRT）")
    _add_lang(p)
    p.add_argument(
        "--probes",
        default=None,
        help="探針數量（例如 4，至少 3）或明確位置秒數（例如 300,1800,4200）",
    )
    p.add_argument("--engine", default=None, help="探針用的轉錄引擎（預設 breeze_ct2）")
    p.add_argument("--model", default=None, help="模型名稱或大小")
    p.add_argument("--model-dir", default=None, help="本機權重目錄")
    p.add_argument(
        "--aligner-dir", default=None, dest="aligner_dir", help="qwen3_asr 對齊器目錄"
    )
    p.add_argument("--backend", default=None, help="引擎後端")
    p.add_argument("--whisper-cpp-bin", default=None, help="whisper.cpp 執行檔路徑")
    p.add_argument("--whisper-cpp-model", default=None, help="whisper.cpp ggml 模型路徑")
    p.add_argument(
        "--allow-cloud", action="store_true", help="允許使用非本機引擎（預設拒絕）"
    )
    p.add_argument(
        "--out-dir", default=None, dest="out_dir", help="輸出目錄（預設與字幕同目錄）"
    )
    p.set_defaults(func=cmd_calibrate_subs)

    p = sub.add_parser("frames", parents=[common], help="抓取投影片換頁影格")
    p.add_argument("video", nargs="?", help="影片路徑")
    p.add_argument(
        "--mode", choices=["scene", "interval"], default="scene", help="抓圖模式"
    )
    p.add_argument("--every", type=float, default=45.0, help="interval 模式的取樣間隔（秒）")
    p.add_argument("--diff-min", type=float, default=4.0, help="相鄰去重門檻")
    p.add_argument(
        "--width", type=int, default=scene_mod.DEFAULT_WIDTH, help="影格寬度（像素）"
    )
    p.add_argument("--stage", action="store_true", help="候選影格先寫入 staging/")
    p.add_argument("--curate", action="store_true", help="策展 staging/ 內的候選影格")
    p.add_argument(
        "--max-per-segment",
        type=int,
        default=curation_mod.DEFAULT_MAX_PER_SEGMENT,
        dest="max_per_segment",
        help="策展時每段最多晉升幾張影格",
    )
    p.set_defaults(func=cmd_frames)

    p = sub.add_parser("ocr", parents=[common], help="對影格做 OCR 並快取結果")
    p.add_argument("target", nargs="?", help="影格資料夾或正式 JSON")
    p.add_argument(
        "--min-conf",
        type=float,
        default=ocr_mod.DEFAULT_MIN_CONF,
        dest="min_conf",
        help="低於此信心值的辨識結果直接丟棄",
    )
    p.add_argument("--no-s2t", action="store_true", help="不做簡轉繁（預設會轉）")
    p.set_defaults(func=cmd_ocr)

    p = sub.add_parser(
        "condense", parents=[common], help="把字幕壓成每分鐘一行，供分段時一次讀完"
    )
    p.add_argument("subtitle", nargs="?", help="字幕路徑（.srt / .vtt）")
    p.add_argument(
        "--window",
        type=int,
        default=condense_mod.DEFAULT_BUCKET_SEC,
        help="每一行涵蓋幾秒（預設 %d）" % condense_mod.DEFAULT_BUCKET_SEC,
    )
    p.add_argument(
        "-o", "--out", default=None, help="輸出路徑（預設 <stem>.condensed.txt）"
    )
    p.set_defaults(func=cmd_condense)

    p = sub.add_parser(
        "scaffold",
        parents=[common],
        help="從字幕建立分段 JSON 骨架（形狀而已，段落語意由 LLM 填寫）",
        description=(
            "從字幕產出形狀合法的 schema v2 骨架：等時間切段、時間欄位算好、"
            "已抓到的影格併入、頂層標成 \"draft\": true。"
            "**段落邊界、標題、摘要、條列這些語意內容由 LLM 填寫**，"
            "本子命令只會在每一個語意欄位寫上 %s 佔位字串。"
            "填完後刪掉 \"draft\" 再跑 l2n check json。"
        ) % scaffold_mod.AI_DRAFT_MARK,
    )
    p.add_argument("target", nargs="?", help="字幕路徑（.srt / .vtt）")
    p.add_argument(
        "--segments",
        type=int,
        default=scaffold_mod.DEFAULT_SEGMENTS,
        help="切成幾段（等時間起始格線；界線之後由 LLM 依主題轉折移動）",
    )
    p.add_argument(
        "--window",
        type=int,
        default=condense_mod.DEFAULT_BUCKET_SEC,
        help="壓縮逐字稿每行涵蓋幾秒（預設 %d）" % condense_mod.DEFAULT_BUCKET_SEC,
    )
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("render", parents=[common], help="從正式 JSON 產出骨架筆記")
    p.add_argument("json_file", nargs="?", help="正式 JSON 路徑")
    p.add_argument(
        "--style", choices=["faithful", "concise"], default=None, help="引用密度風格"
    )
    p.add_argument(
        "--expand-prompt", action="store_true", help="印出 LLM 擴寫指令包後結束"
    )
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("viewer", parents=[common], help="產出三層同步 viewer HTML")
    p.add_argument("json_file", nargs="?", help="正式 JSON 路徑")
    p.add_argument(
        "--preflight",
        action="store_true",
        help="只列出將建立或覆蓋的檔案，不寫入任何東西",
    )
    p.set_defaults(func=cmd_viewer)

    p = sub.add_parser("pbf", parents=[common], help="產出 PotPlayer 章節檔（需在設定開啟）")
    p.add_argument("json_file", nargs="?", help="正式 JSON 路徑")
    p.set_defaults(func=cmd_pbf)

    p = sub.add_parser("hub", parents=[common], help="產出課程首頁與跨講座搜尋索引")
    p.add_argument("folder", nargs="?", help="課程資料夾")
    p.add_argument("--title", default=None, help="課程名稱（預設用資料夾名）")
    p.add_argument(
        "--preflight",
        action="store_true",
        help="只列出將建立或覆蓋的檔案，不寫入任何東西",
    )
    p.set_defaults(func=cmd_hub)

    p = sub.add_parser(
        "publish", parents=[common], help="把一場講座的衍生產物交易式發佈到目標資料夾"
    )
    p.add_argument("stem", nargs="?", help="講座 stem 或它的任一個檔案路徑")
    p.add_argument("--dest", default=None, help="目標資料夾（必須與來源同一個檔案系統）")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("check", parents=[common], help="執行各階段的驗收合約")
    p.add_argument(
        "what",
        nargs="?",
        help="要檢查的階段：transcribe / frames / json / note；加 --all 時填 <stem>",
    )
    p.add_argument("target", nargs="?", help="檔案或資料夾路徑")
    p.add_argument(
        "--all",
        dest="all_stages",
        action="store_true",
        help="對一個 <stem> 執行所有有產物的階段，exit code 取最大值",
    )
    p.add_argument(
        "--report", default=None, help="把 --all 的結果寫成 JSON 稽核報告"
    )
    p.add_argument(
        "--note", default=None, help="check note 的筆記路徑（預設 <stem>.v4.md）"
    )
    p.add_argument(
        "--style",
        choices=["faithful", "concise"],
        default=None,
        help="check note 的風格；faithful 才檢查 R6（預設取 profile）",
    )
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("migrate", parents=[common], help="把舊版 JSON 原地升級為 schema v2")
    p.add_argument("json_file", nargs="?", help="正式 JSON 路徑")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("run", parents=[common], help="依序串接所有機械階段")
    p.add_argument("video", nargs="?", help="影片路徑")
    _add_lang(p)
    p.add_argument("--engine", default=None, help="轉錄引擎（預設 breeze_ct2）")
    p.add_argument("--model", default=None, help="模型名稱或大小")
    p.add_argument(
        "--allow-cloud", action="store_true", help="允許使用非本機引擎（預設拒絕）"
    )
    p.add_argument(
        "--style",
        choices=["faithful", "concise"],
        default=None,
        help="骨架筆記與 check note 的風格（預設取 profile）",
    )
    p.add_argument(
        "--mode", choices=["scene", "interval"], default=None,
        help="抓圖模式（預設 scene）",
    )
    p.add_argument(
        "--every", type=float, default=None, help="interval 模式的取樣秒數"
    )
    p.add_argument(
        "--preflight",
        action="store_true",
        help="只列出將建立或覆蓋的檔案，不寫入任何東西",
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser(
        "convert-model", parents=[common], help="把 Breeze-ASR-25 轉為 CTranslate2 權重"
    )
    p.add_argument("--src", default=None, help="來源模型（HuggingFace id 或本機目錄）")
    p.add_argument("--out", default=None, help="輸出目錄（預設在 whisper-models 下）")
    p.add_argument(
        "--quantization",
        default=convert.DEFAULT_QUANTIZATION,
        choices=list(convert.QUANTIZATIONS),
        help="量化方式（預設 float16）",
    )
    p.set_defaults(func=cmd_convert_model)

    p = sub.add_parser("profile", parents=[common], help="檢視 profile 與 overlay 的生效設定")
    p.add_argument("action", nargs="?", default="show", help="子動作：show")
    # `--profile` itself comes from the shared option group above.
    p.add_argument(
        "--style",
        choices=["faithful", "concise"],
        default=None,
        help="以這個風格當作 cli 層來看生效設定",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="以 JSON 物件輸出，每鍵含 value 與 source",
    )
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser(
        "install-skill", parents=[common], help="把 skill/ 部署到三家 agent 的技能目錄"
    )
    p.add_argument(
        "--target",
        choices=["claude", "codex", "opencode"],
        default=None,
        help="部署目標",
    )
    p.add_argument("--all", action="store_true", help="三家一次部署")
    p.add_argument("--dest", default=None, help="自訂目標目錄")
    p.add_argument("--check", action="store_true", help="比對內容 hash 回報 drift")
    p.set_defaults(func=cmd_install_skill)

    return parser


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    _out.configure(
        quiet=getattr(args, "quiet", False),
        json_progress=getattr(args, "json_progress", False),
    )

    func: Optional[Callable[[argparse.Namespace], int]] = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return exit_codes.OK

    try:
        # The cli layer is installed around the dispatch rather than read
        # inside each stage, so `--profile` reaches the resolver from every
        # subcommand without every stage body having to pass it along. It is
        # scoped to this one call: two commands in one process (which is what
        # an in-process CLI test is) must not inherit each other's flags.
        with loader.cli_layer(profile=getattr(args, "profile", None)):
            return int(func(args))
    except loader.OverlayError as exc:
        # A configuration file exists but does not parse. Naming the file and
        # the line is the whole contract: the alternative is running with the
        # defaults under the user's own settings' name.
        _out.error(exc.message())
        return exit_codes.ERROR
    except StageNotImplemented as exc:
        _out.stage(exc.name, "not implemented yet")
        return exit_codes.NOT_IMPLEMENTED
    except _deps.MissingDependency as exc:
        exc.report()
        return exit_codes.MISSING_DEPENDENCY
    except RuntimeError as exc:
        # An external tool or an engine failed. The message already says what
        # and where, so print it as an error instead of a traceback.
        _out.error(str(exc))
        return exit_codes.ERROR
    except SystemExit as exc:
        code = exc.code
        return exit_codes.OK if code is None else int(code)
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        _out.error("使用者中斷")
        return exit_codes.ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
