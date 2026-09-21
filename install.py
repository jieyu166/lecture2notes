#!/usr/bin/env python3
"""Install this repository's ``skill/`` directory into the three agent folders.

Usable without installing the package:

    python install.py --all
    python install.py --target claude
    python install.py --dest D:/somewhere/lecture2notes
    python install.py --check --all

Identical in behaviour to ``l2n install-skill``; both call
``lecture2notes.install``. This wrapper exists so that someone who has only
cloned the repository can deploy the skill before deciding whether to install
the Python package at all, which is why it puts ``src/`` on ``sys.path``
itself instead of assuming an editable install.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SRC = REPO_ROOT / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from lecture2notes import install as install_mod  # noqa: E402
from lecture2notes import exit_codes  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="把 skill/ 部署到 Claude Code / Codex / OpenCode 的技能目錄",
    )
    parser.add_argument(
        "--target",
        choices=list(install_mod.TARGET_NAMES),
        default=None,
        help="部署目標",
    )
    parser.add_argument("--all", action="store_true", help="三家一次部署")
    parser.add_argument("--dest", default=None, help="自訂目標目錄")
    parser.add_argument(
        "--check", action="store_true", help="比對內容 hash 回報 drift（不一致 exit 2）"
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.check:
            lines, all_ok = install_mod.check(
                target=args.target, all_targets=args.all, dest=args.dest
            )
            for line in lines:
                print(line)
            return exit_codes.OK if all_ok else exit_codes.ERROR

        results = install_mod.install(
            target=args.target, all_targets=args.all, dest=args.dest
        )
    except install_mod.InstallError as exc:
        print("[error] %s" % exc, file=sys.stderr)
        return exit_codes.ERROR

    for _label, path, skipped in results:
        print(path)
        for name in skipped:
            print("[install-skill] kept existing overlay file: %s" % name)
    return exit_codes.OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
