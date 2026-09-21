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
from lecture2notes.outputs import viewer as viewer_mod
from lecture2notes.profiles import loader
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
    "scaffold",
    "render",
    "viewer",
    "pbf",
    "hub",
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
    result = ocr_mod.run_ocr(
        path,
        min_conf=getattr(args, "min_conf", ocr_mod.DEFAULT_MIN_CONF),
        s2t=not getattr(args, "no_s2t", False),
        force=getattr(args, "force", False),
    )
    _out.ok(
        "ocr: %d frames, %d with text" % (result.total, len(result.recognised))
    )
    return exit_codes.OK


def cmd_scaffold(args: argparse.Namespace) -> int:
    not_implemented("scaffold")
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

    profile = str(data.get("profile") or loader.BUILTIN_PROFILE)
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


def cmd_run(args: argparse.Namespace) -> int:
    require_lang(args)
    _deps.require("ffmpeg")
    not_implemented("run")
    return exit_codes.OK


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


def cmd_profile(args: argparse.Namespace) -> int:
    not_implemented("profile")
    return exit_codes.OK


def cmd_install_skill(args: argparse.Namespace) -> int:
    not_implemented("install-skill")
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

    p = sub.add_parser("scaffold", parents=[common], help="從字幕建立分段 JSON 骨架")
    p.add_argument("target", nargs="?", help="字幕或影片路徑")
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
    p.add_argument("--profile", default=None, help="profile 名稱")
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
        return int(func(args))
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
