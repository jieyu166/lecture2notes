"""Console output and progress reporting.

Every user-visible line in this package goes through this module. Two hard rules
are enforced here so they cannot drift:

1. Markers are ASCII only: ``[ok] [warn] [error] [skip] ->``. The characters
   U+2192, U+2265, U+2713 and U+2717 must never appear in this package, because a
   Windows cp950 console cannot encode them.
2. Message text may be Traditional Chinese, but writing is guarded so a console
   with a narrow codepage degrades characters instead of raising
   UnicodeEncodeError.

Long stages report progress through :class:`Progress`, which emits either a plain
line (``[frames] 12/134 elapsed 31s eta 290s``) or, under ``--json-progress``, one
JSON object per line on stdout.
"""

from __future__ import annotations

import json
import sys
import time
from typing import IO, Iterable, Iterator, Optional, TypeVar

LEVELS = ("ok", "warn", "error", "skip", "info")

_quiet = False
_json_progress = False

T = TypeVar("T")


def configure(quiet: bool = False, json_progress: bool = False) -> None:
    """Apply the global output flags parsed from the command line."""
    global _quiet, _json_progress
    _quiet = bool(quiet)
    _json_progress = bool(json_progress)


def is_quiet() -> bool:
    return _quiet


def is_json_progress() -> bool:
    return _json_progress


def reset() -> None:
    """Restore defaults (used by tests)."""
    configure(False, False)


def _write(stream: IO[str], text: str) -> None:
    """Write one line, never raising UnicodeEncodeError on a narrow console."""
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        safe = text.encode(encoding, "replace").decode(encoding, "replace")
        stream.write(safe + "\n")
    try:
        stream.flush()
    except Exception:  # pragma: no cover - closed stream during interpreter exit
        pass


def _message_stream() -> IO[str]:
    """Messages move to stderr under --json-progress so stdout stays pure JSON."""
    return sys.stderr if _json_progress else sys.stdout


def say(level: str, msg: str) -> None:
    """Print ``[level] msg``. Unknown levels fall back to ``[info]``."""
    tag = level if level in LEVELS else "info"
    if _quiet and tag not in ("warn", "error"):
        return
    _write(_message_stream(), "[%s] %s" % (tag, msg))


def stage(name: str, msg: str) -> None:
    """Print a stage-tagged line, for example ``[frames] skip (exists)``."""
    if _quiet:
        return
    _write(_message_stream(), "[%s] %s" % (name, msg))


def error(msg: str) -> None:
    say("error", msg)


def warn(msg: str) -> None:
    say("warn", msg)


def ok(msg: str) -> None:
    say("ok", msg)


def skip(msg: str) -> None:
    say("skip", msg)


class Progress:
    """Progress reporter for any stage that can run longer than 10 seconds.

    A line is emitted on the first unit, on the last unit, and whenever
    ``interval`` seconds have passed since the previous line. Pass ``interval=0``
    to report every single unit.
    """

    def __init__(
        self,
        stage: str,
        total: int,
        interval: float = 5.0,
        clock=time.monotonic,
    ) -> None:
        self.stage = stage
        self.total = max(int(total), 0)
        self.interval = float(interval)
        self.done = 0
        self._clock = clock
        self._start = clock()
        self._last_emit: Optional[float] = None

    # -- internals ---------------------------------------------------------
    def _elapsed(self) -> float:
        return max(self._clock() - self._start, 0.0)

    def _eta(self, elapsed: float) -> Optional[float]:
        if self.done <= 0 or self.total <= 0 or self.done >= self.total:
            return 0.0 if self.done >= self.total and self.total > 0 else None
        return elapsed * (self.total - self.done) / float(self.done)

    def _should_emit(self, now: float) -> bool:
        if self.done <= 1:
            return True
        if self.total and self.done >= self.total:
            return True
        if self.interval <= 0:
            return True
        return self._last_emit is None or (now - self._last_emit) >= self.interval

    def _emit(self) -> None:
        elapsed = self._elapsed()
        eta = self._eta(elapsed)
        if _json_progress:
            event = {
                "stage": self.stage,
                "done": self.done,
                "total": self.total,
                "elapsed_sec": round(elapsed, 3),
                "eta_sec": None if eta is None else round(eta, 3),
            }
            _write(sys.stdout, json.dumps(event, ensure_ascii=False))
            return
        eta_text = "?" if eta is None else str(int(round(eta)))
        _write(
            sys.stdout,
            "[%s] %d/%d elapsed %ds eta %ss"
            % (self.stage, self.done, self.total, int(round(elapsed)), eta_text),
        )

    # -- public API --------------------------------------------------------
    def advance(self, n: int = 1) -> None:
        """Record ``n`` completed units and report if it is time to."""
        self.done += int(n)
        if _quiet:
            return
        now = self._clock()
        if self._should_emit(now):
            self._last_emit = now
            self._emit()

    def finish(self) -> None:
        """Force a final line even if the last ``advance`` was throttled."""
        if _quiet:
            return
        self._last_emit = self._clock()
        self._emit()

    def wrap(self, items: Iterable[T]) -> Iterator[T]:
        """Yield ``items``, advancing after each one."""
        for item in items:
            yield item
            self.advance()


__all__ = [
    "LEVELS",
    "Progress",
    "configure",
    "error",
    "is_json_progress",
    "is_quiet",
    "ok",
    "reset",
    "say",
    "skip",
    "stage",
    "warn",
]
