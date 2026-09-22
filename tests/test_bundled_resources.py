"""The three resources that live outside `src/` must be reachable as resources.

v0.2.0 found `skill/`, `docs/note-writing-guideline.md` and
`examples/overlay-minimal/` by walking up from the package to the repository
root. That is true in a checkout and false in a wheel, and nothing in the suite
noticed, because the suite only ever runs in a checkout. So these tests do not
ask "can the file be found" -- that would pass on the broken version too. They
ask the two questions a checkout can actually answer:

* every file is reachable through :mod:`importlib.resources` from the package,
  once the bundled layout exists (asserted against a synthesised one, so the
  lookup code is exercised rather than the repository's own directory tree);
* the repository original wins when both are present, which is what keeps one
  editable copy of each file rather than two.

Whether a real wheel actually carries them is a fact about the build, and no
amount of unit testing establishes it. `tests/test_packaging_wheel.py` builds
one and looks.
"""

from __future__ import annotations

import shutil
from importlib import resources as importlib_resources
from pathlib import Path
from typing import List

import pytest

from lecture2notes import resources
from lecture2notes.notes import guideline
from lecture2notes.profiles import bootstrap, layers

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The six reference documents `SKILL.md` routes to. Named rather than globbed:
#: a glob would go on passing after someone deleted one.
SKILL_REFERENCES = (
    "frames-and-notes.md",
    "note-writing.md",
    "outputs-and-batch.md",
    "profiles-and-overlay.md",
    "segmentation.md",
    "transcription.md",
)


def _package_dir() -> Path:
    """The installed package directory, via importlib.resources."""
    import os

    return Path(os.fspath(importlib_resources.files("lecture2notes")))


@pytest.fixture
def bundled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A package directory laid out the way a built wheel lays one out.

    The repository lookup is disabled for the duration, so what the resolver
    finds can only have come through the bundled path.
    """
    package = tmp_path / "lecture2notes"
    bundle = package / resources.BUNDLE_DIRNAME
    (bundle / "docs").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "skill", bundle / "skill")
    shutil.copytree(
        REPO_ROOT / "examples" / "overlay-minimal",
        bundle / "examples" / "overlay-minimal",
    )
    shutil.copyfile(
        REPO_ROOT / "docs" / "note-writing-guideline.md",
        bundle / "docs" / "note-writing-guideline.md",
    )
    monkeypatch.setattr(resources, "bundle_root", lambda: bundle)
    monkeypatch.setattr(resources, "repo_root", lambda start=None: None)
    return bundle


# --------------------------------------------------------------------------
# the package really is where importlib.resources says it is
# --------------------------------------------------------------------------
def test_the_package_directory_resolves_through_importlib_resources():
    assert (_package_dir() / "resources.py").is_file()


def test_a_checkout_has_no_bundled_directory():
    """The build makes `_bundled/`; the working tree must never carry one.

    Two copies of the same file in one repository is the failure this change
    was supposed to avoid, and it would look exactly like this.
    """
    assert not (REPO_ROOT / "src" / "lecture2notes" / resources.BUNDLE_DIRNAME).exists()


# --------------------------------------------------------------------------
# every file is readable from the bundled layout
# --------------------------------------------------------------------------
def test_the_skill_document_is_readable_from_the_bundle(bundled: Path):
    found = resources.skill_dir()
    assert found == bundled / "skill"
    text = (found / "SKILL.md").read_text(encoding="utf-8")
    assert text.strip(), "SKILL.md is empty"


def test_all_six_references_are_readable_from_the_bundle(bundled: Path):
    found = resources.skill_dir()
    missing: List[str] = []
    for name in SKILL_REFERENCES:
        path = found / "references" / name
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            missing.append(name)
    assert missing == []


def test_the_guideline_document_is_readable_from_the_bundle(bundled: Path):
    found = resources.guideline_doc()
    assert found == bundled / "docs" / "note-writing-guideline.md"
    assert guideline.VERSION_LINE in found.read_text(encoding="utf-8")


def test_the_overlay_example_is_readable_from_the_bundle(bundled: Path):
    found = resources.overlay_example_dir()
    assert found == bundled / "examples" / "overlay-minimal"
    for name in layers.OVERLAY_FILES:
        assert (found / name).is_file(), name


def test_the_expand_prompt_quotes_the_rules_from_the_bundle(
    bundled: Path, tmp_path: Path
):
    """The failure this fixes is silent: the bundle prints a path, not rules."""
    out = guideline.expand_prompt(tmp_path / "lecture.json")
    assert guideline.MUST_FOLLOW_HEADING in out


def test_install_reads_its_file_list_from_the_bundle(bundled: Path):
    from lecture2notes import install as install_mod

    names = install_mod.iter_source_files()
    assert "SKILL.md" in names
    for name in SKILL_REFERENCES:
        assert "references/%s" % name in names


# --------------------------------------------------------------------------
# the repository original wins
# --------------------------------------------------------------------------
def test_the_repository_copy_is_preferred_over_the_bundled_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    bundle = tmp_path / "bundle"
    (bundle / "skill").mkdir(parents=True)
    (bundle / "skill" / "SKILL.md").write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(resources, "bundle_root", lambda: bundle)

    assert resources.skill_dir() == REPO_ROOT / "skill"


def test_the_repository_root_is_this_checkout():
    assert resources.repo_root() == REPO_ROOT


def test_nothing_found_anywhere_is_none_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(resources, "bundle_root", lambda: None)
    monkeypatch.setattr(resources, "repo_root", lambda start=None: None)

    assert resources.skill_dir() is None
    assert resources.guideline_doc() is None
    assert resources.overlay_example_dir() is None


def test_install_still_reports_a_missing_skill_directory(
    monkeypatch: pytest.MonkeyPatch,
):
    from lecture2notes import install as install_mod

    monkeypatch.setattr(resources, "bundle_root", lambda: None)
    monkeypatch.setattr(resources, "repo_root", lambda start=None: None)

    with pytest.raises(install_mod.InstallError) as info:
        install_mod.source_dir()
    assert "skill source directory not found" in str(info.value)


def test_bootstrap_reports_a_missing_example(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(resources, "bundle_root", lambda: None)
    monkeypatch.setattr(resources, "repo_root", lambda start=None: None)

    with pytest.raises(bootstrap.BootstrapError) as info:
        bootstrap.example_dir()
    assert "overlay example not found" in str(info.value)
