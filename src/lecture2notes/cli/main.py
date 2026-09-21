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
import sys
from typing import Callable, List, Optional, Sequence

from lecture2notes import __version__, _deps, _out, exit_codes
from lecture2notes.engines import registry

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
# stage handlers
# --------------------------------------------------------------------------
def cmd_transcribe(args: argparse.Namespace) -> int:
    if getattr(args, "list_engines", False):
        for text in registry.engine_lines():
            _out.line(text)
        return exit_codes.OK
    require_lang(args)
    _deps.require("ffmpeg")
    not_implemented("transcribe")
    return exit_codes.OK


def cmd_calibrate_subs(args: argparse.Namespace) -> int:
    _deps.require("ffmpeg")
    not_implemented("calibrate-subs")
    return exit_codes.OK


def cmd_frames(args: argparse.Namespace) -> int:
    _deps.require("ffmpeg")
    _deps.require("ffprobe")
    not_implemented("frames")
    return exit_codes.OK


def cmd_ocr(args: argparse.Namespace) -> int:
    _deps.require("rapidocr")
    not_implemented("ocr")
    return exit_codes.OK


def cmd_scaffold(args: argparse.Namespace) -> int:
    not_implemented("scaffold")
    return exit_codes.OK


def cmd_render(args: argparse.Namespace) -> int:
    not_implemented("render")
    return exit_codes.OK


def cmd_viewer(args: argparse.Namespace) -> int:
    not_implemented("viewer")
    return exit_codes.OK


def cmd_pbf(args: argparse.Namespace) -> int:
    not_implemented("pbf")
    return exit_codes.OK


def cmd_hub(args: argparse.Namespace) -> int:
    not_implemented("hub")
    return exit_codes.OK


def cmd_check(args: argparse.Namespace) -> int:
    not_implemented("check")
    return exit_codes.OK


def cmd_migrate(args: argparse.Namespace) -> int:
    not_implemented("migrate")
    return exit_codes.OK


def cmd_run(args: argparse.Namespace) -> int:
    require_lang(args)
    _deps.require("ffmpeg")
    not_implemented("run")
    return exit_codes.OK


def cmd_convert_model(args: argparse.Namespace) -> int:
    not_implemented("convert-model")
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
        "--allow-cloud", action="store_true", help="允許使用非本機引擎（預設拒絕）"
    )
    p.add_argument(
        "--whisper-cpp-bin", default=None, help="whisper.cpp 執行檔路徑"
    )
    p.add_argument(
        "--whisper-cpp-model", default=None, help="whisper.cpp 的 ggml 模型檔路徑"
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
    p.add_argument("--probes", type=int, default=3, help="探針數量（至少 3）")
    p.set_defaults(func=cmd_calibrate_subs)

    p = sub.add_parser("frames", parents=[common], help="抓取投影片換頁影格")
    p.add_argument("video", nargs="?", help="影片路徑")
    p.add_argument(
        "--mode", choices=["scene", "interval"], default="scene", help="抓圖模式"
    )
    p.add_argument("--every", type=float, default=45.0, help="interval 模式的取樣間隔（秒）")
    p.add_argument("--diff-min", type=float, default=4.0, help="相鄰去重門檻")
    p.add_argument("--stage", action="store_true", help="候選影格先寫入 staging/")
    p.add_argument("--curate", action="store_true", help="策展 staging/ 內的候選影格")
    p.set_defaults(func=cmd_frames)

    p = sub.add_parser("ocr", parents=[common], help="對影格做 OCR 並快取結果")
    p.add_argument("target", nargs="?", help="影格資料夾或正式 JSON")
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
    p.set_defaults(func=cmd_viewer)

    p = sub.add_parser("pbf", parents=[common], help="產出 PotPlayer 章節檔（需在設定開啟）")
    p.add_argument("json_file", nargs="?", help="正式 JSON 路徑")
    p.set_defaults(func=cmd_pbf)

    p = sub.add_parser("hub", parents=[common], help="產出課程首頁與跨講座搜尋索引")
    p.add_argument("folder", nargs="?", help="課程資料夾")
    p.set_defaults(func=cmd_hub)

    p = sub.add_parser("check", parents=[common], help="執行各階段的驗收合約")
    p.add_argument(
        "what", nargs="?", help="要檢查的階段：transcribe / frames / json / note"
    )
    p.add_argument("target", nargs="?", help="檔案或資料夾路徑")
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
    p.add_argument("--out", default=None, help="輸出目錄")
    p.add_argument(
        "--quantization", default="float16", help="量化方式（預設 float16）"
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
    except SystemExit as exc:
        code = exc.code
        return exit_codes.OK if code is None else int(code)
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        _out.error("使用者中斷")
        return exit_codes.ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
