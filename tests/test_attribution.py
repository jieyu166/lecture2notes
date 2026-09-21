"""ATTRIBUTION.md and NOTICE stay consistent with the source headers they describe.

For every module ATTRIBUTION.md's table marks relation=inspired, the module's
own docstring header (within its first 15 lines) must carry both the
"Inspired by drpwchen/lecture-to-notes" marker and the exact upstream path
ATTRIBUTION.md cites for it. NOTICE must carry the upstream copyright line
verbatim, so the MIT attribution requirement is satisfied even if someone
reads only one of the two files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ATTRIBUTION_PATH = REPO_ROOT / "ATTRIBUTION.md"
NOTICE_PATH = REPO_ROOT / "NOTICE"
SRC_ROOT = REPO_ROOT / "src" / "lecture2notes"

HEADER_LINES = 15
INSPIRED_MARKER = "Inspired by drpwchen/lecture-to-notes"

# Matches a data row of the "Module table" in ATTRIBUTION.md, e.g.:
# | `frames/ocr.py` | inspired | `scripts/quick_ocr.py` | Same stage ... |
ROW_RE = re.compile(
    r"^\|\s*`(?P<module>[^`]+)`\s*\|\s*(?P<relation>[a-z-]+)\s*\|\s*"
    r"(?P<upstream>`[^`]+`|—)\s*\|"
)


def _parse_attribution_table(text: str) -> list[dict[str, str]]:
    rows = []
    for line in text.splitlines():
        m = ROW_RE.match(line.strip())
        if not m:
            continue
        upstream = m.group("upstream")
        upstream = None if upstream == "—" else upstream.strip("`")
        rows.append(
            {
                "module": m.group("module"),
                "relation": m.group("relation"),
                "upstream": upstream,
            }
        )
    return rows


@pytest.fixture(scope="module")
def attribution_text() -> str:
    assert ATTRIBUTION_PATH.exists(), "ATTRIBUTION.md is missing"
    return ATTRIBUTION_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def attribution_rows(attribution_text: str) -> list[dict[str, str]]:
    rows = _parse_attribution_table(attribution_text)
    assert rows, "no rows parsed out of ATTRIBUTION.md's module table"
    return rows


@pytest.fixture(scope="module")
def notice_text() -> str:
    assert NOTICE_PATH.exists(), "NOTICE is missing"
    return NOTICE_PATH.read_text(encoding="utf-8")


def test_attribution_table_lists_every_source_module(attribution_rows):
    listed = {row["module"] for row in attribution_rows}
    actual = {
        p.relative_to(SRC_ROOT).as_posix()
        for p in SRC_ROOT.rglob("*.py")
    }
    missing = actual - listed
    assert not missing, f"modules missing from ATTRIBUTION.md table: {sorted(missing)}"
    stale = listed - actual
    assert not stale, f"ATTRIBUTION.md lists modules that no longer exist: {sorted(stale)}"


def test_inspired_rows_require_upstream_path(attribution_rows):
    for row in attribution_rows:
        if row["relation"] == "inspired":
            assert row["upstream"], (
                f"{row['module']} is marked inspired but has no upstream path "
                "in ATTRIBUTION.md"
            )


_INSPIRED_ROWS = [
    row
    for row in _parse_attribution_table(ATTRIBUTION_PATH.read_text(encoding="utf-8"))
    if row["relation"] == "inspired"
]


@pytest.mark.parametrize(
    "row", _INSPIRED_ROWS, ids=[row["module"] for row in _INSPIRED_ROWS]
)
def test_inspired_file_header_carries_attribution(row):
    module_path = SRC_ROOT / row["module"]
    assert module_path.exists(), f"{row['module']} listed in ATTRIBUTION.md does not exist"

    header = "".join(module_path.read_text(encoding="utf-8").splitlines(keepends=True)[:HEADER_LINES])

    assert INSPIRED_MARKER in header, (
        f"{row['module']}: first {HEADER_LINES} lines are missing "
        f"'{INSPIRED_MARKER}'"
    )
    assert row["upstream"] in header, (
        f"{row['module']}: first {HEADER_LINES} lines are missing the upstream "
        f"path '{row['upstream']}' that ATTRIBUTION.md cites for it"
    )


def test_at_least_one_inspired_module_is_checked():
    # Guards against the parametrized test above silently collecting zero
    # cases if the table's markdown format ever changes shape.
    assert len(_INSPIRED_ROWS) >= 5


def test_notice_contains_upstream_copyright_line(notice_text):
    assert "Copyright (c) 2026 drpwchen" in notice_text


def test_notice_contains_upstream_license_body(notice_text):
    assert "MIT License" in notice_text
    assert "https://github.com/drpwchen/lecture-to-notes" in notice_text
    assert "THE SOFTWARE IS PROVIDED \"AS IS\"" in notice_text


def test_notice_contains_asr_benchmark_copyright_line(notice_text):
    # engines/faster_whisper.py and engines/breeze_ct2.py credit
    # drpwchen/asr-benchmark by name for two default parameter values; its
    # license must be reproduced here too since it is a separate repo.
    assert "Copyright (c) 2026 Po-Wei Chen" in notice_text
    assert "https://github.com/drpwchen/asr-benchmark" in notice_text
