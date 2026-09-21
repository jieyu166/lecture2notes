"""What a command would write, listed without writing it.

`--preflight` exists because the destructive part of this pipeline is not the
computation, it is the overwrite: a viewer or a hub page is regenerated in place,
and a run that silently replaces a hand-edited file is indistinguishable from one
that created it. Listing the targets and their current state first makes the
difference visible before anything is touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

CREATE = "create"
OVERWRITE = "overwrite"


@dataclass(frozen=True)
class PlannedFile:
    """One file a real run would write, and whether it is there now."""

    path: str
    exists: bool
    action: str

    def line(self) -> str:
        """``[preflight] overwrite <path> (exists)`` -- ASCII markers only."""
        return "%s %s (%s)" % (
            self.action, self.path, "exists" if self.exists else "new"
        )


def plan_for(paths: Iterable[Path]) -> List[PlannedFile]:
    """Turn target paths into a plan. Reads the filesystem, writes nothing."""
    planned: List[PlannedFile] = []
    for path in paths:
        target = Path(path)
        exists = target.exists()
        planned.append(
            PlannedFile(str(target), exists, OVERWRITE if exists else CREATE)
        )
    return planned


__all__ = ["CREATE", "OVERWRITE", "PlannedFile", "plan_for"]
