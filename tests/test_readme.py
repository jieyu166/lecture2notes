"""README's acknowledgement section names the upstream repo and the required wording."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
UPSTREAM_URL = "https://github.com/drpwchen/lecture-to-notes"


def _readme_text() -> str:
    assert README_PATH.exists(), "README.md is missing"
    return README_PATH.read_text(encoding="utf-8")


def test_readme_links_upstream_repo():
    text = _readme_text()
    assert UPSTREAM_URL in text


def test_readme_has_adapted_from_wording():
    text = _readme_text()
    assert ("修改自" in text) or ("Adapted from" in text)


def test_readme_has_single_acknowledgement_section():
    text = _readme_text()
    # Exactly one "## 致謝" heading -- the section was rewritten in place,
    # not duplicated alongside an earlier draft.
    assert text.count("## 致謝") == 1


def test_readme_references_attribution_file():
    text = _readme_text()
    assert "ATTRIBUTION.md" in text
