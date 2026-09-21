"""Detect the ASR failure mode that looks exactly like a working transcript.

New module for this package. Every Whisper-family model, including the ones this
package ships, can fall into a loop: it emits the same short phrase for every cue
until the audio ends. On music, on applause, on a long silence, on a corrupted
tail. The output is a perfectly well-formed SRT with correct timecodes, so
nothing downstream notices, and the loop ends up quoted in the notes.

The signal is unambiguous and cheap: a run of identical cue text. Real speech
repeats a phrase twice, sometimes three times. Thirty consecutive identical cues
is not speech.

This is reported as a warning rather than an error on purpose. The transcript is
still usable up to the point where the loop starts, and the user is the one who
decides whether to re-run with different settings or to trim the tail; refusing
to continue would throw away a good hour of transcription over a bad minute.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from lecture2notes.engines.base import Cue, parse_srt

#: Consecutive identical cues at or above this count are a loop.
THRESHOLD = 30


@dataclass(frozen=True)
class Loop:
    """One run of identical cues, numbered the way an SRT numbers them."""

    first_cue: int
    last_cue: int
    count: int
    text: str

    def message(self) -> str:
        """The exact line the acceptance report prints."""
        return "hallucination loop cues %d..%d (%d identical)" % (
            self.first_cue, self.last_cue, self.count
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "first_cue": self.first_cue,
            "last_cue": self.last_cue,
            "count": self.count,
            "text": self.text,
        }


def find_loops(cues: Sequence[Cue], threshold: int = THRESHOLD) -> List[Loop]:
    """Every run of at least ``threshold`` cues with identical text.

    Cue numbers are 1-based so they match what a subtitle editor shows, which is
    the whole point of reporting them: the user has to go and look.
    """
    loops: List[Loop] = []
    if not cues:
        return loops
    run_start = 0
    for position in range(1, len(cues) + 1):
        if position < len(cues) and cues[position].text == cues[run_start].text:
            continue
        length = position - run_start
        if length >= threshold:
            loops.append(
                Loop(
                    first_cue=run_start + 1,
                    last_cue=position,
                    count=length,
                    text=cues[run_start].text,
                )
            )
        run_start = position
    return loops


def find_loops_in_file(path: Path, threshold: int = THRESHOLD) -> List[Loop]:
    return find_loops(parse_srt(Path(path)), threshold)


def messages(loops: Sequence[Loop]) -> List[str]:
    return [loop.message() for loop in loops]


__all__ = [
    "THRESHOLD",
    "Loop",
    "find_loops",
    "find_loops_in_file",
    "messages",
]
