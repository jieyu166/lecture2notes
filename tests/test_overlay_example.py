"""`examples/overlay-minimal/` is a working overlay, not a screenshot of one.

An example that has drifted out of sync with the loader is worse than no
example: the person copies it, nothing happens, and they conclude the feature
does not work. So this copies the shipped folder into a throwaway home and
holds it to the only claim the README makes -- every key it defines is reported
with source layer `user`, and nothing fails to parse.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List

import pytest

from lecture2notes import exit_codes
from lecture2notes.cli.main import main
from lecture2notes.profiles import layers, loader

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "overlay-minimal"


@pytest.fixture
def installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The example copied to `~/.lecture2notes/`, exactly as the README says."""
    home = tmp_path / "home"
    target = home / layers.OVERLAY_DIR
    target.mkdir(parents=True)
    for name in layers.OVERLAY_FILES:
        shutil.copyfile(EXAMPLE / name, target / name)

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(work)
    return target


def defined_keys() -> List[str]:
    """Every setting name the example files actually define."""
    keys: List[str] = []
    for dotted, _value in layers.flatten(layers.read_toml(EXAMPLE / layers.OUTPUTS_FILE)):
        if dotted != loader.PROFILE_KEY:
            keys.append(dotted)
    keys.extend(
        dotted
        for dotted, _value in layers.flatten(
            layers.read_toml(EXAMPLE / layers.PRIVACY_FILE), loader.PRIVACY_PREFIX
        )
    )
    keys.extend(
        dotted
        for dotted, _value in layers.flatten(
            layers.read_json(EXAMPLE / layers.CORRECTIONS_FILE),
            loader.CORRECTIONS_PREFIX,
        )
    )
    keys.extend(loader.TEMPLATE_PREFIX + name for name in layers.TEMPLATE_FILES)
    return keys


# --------------------------------------------------------------------------
# the folder itself
# --------------------------------------------------------------------------
def test_the_example_ships_one_of_each_overlayable_file():
    for name in layers.OVERLAY_FILES:
        assert (EXAMPLE / name).is_file(), "examples/overlay-minimal/%s is missing" % name


def test_the_example_contains_nothing_that_is_not_documented():
    allowed = set(layers.OVERLAY_FILES) | {"README.md"}
    actual = {p.name for p in EXAMPLE.iterdir()}
    assert actual == allowed


def test_the_frontmatter_example_explains_its_own_extra_field():
    text = (EXAMPLE / layers.FRONTMATTER_TEMPLATE).read_text(encoding="utf-8")
    assert "消化層級: 1" in text
    for level in ("0 =", "1 =", "2 =", "3 ="):
        assert level in text, "the 0-3 scale is not explained: %s" % level


# --------------------------------------------------------------------------
# the claim the README makes
# --------------------------------------------------------------------------
def test_every_key_the_example_defines_comes_from_the_user_layer(
    installed: Path, capsys
):
    assert main(["profile", "show", "--json"]) == exit_codes.OK
    payload: Dict[str, Dict[str, object]] = json.loads(capsys.readouterr().out)

    expected = defined_keys()
    assert expected, "the example defines no keys at all"
    for key in expected:
        assert key in payload, "profile show never mentions %s" % key
        assert payload[key]["source"] == "user", (key, payload[key])


def test_installing_the_example_produces_no_parse_error(installed: Path, capsys):
    assert main(["profile", "show"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "[error]" not in out


def test_the_example_actually_changes_the_answers(installed: Path):
    # An example whose every value happens to match the default would pass the
    # source test above while proving nothing.
    assert loader.note_style() == "faithful"
    assert loader.transcript_paste_severity() == "error"
    assert loader.frontmatter_template().count("消化層級") == 1
    assert "我的話" in loader.note_template()


def test_the_profile_is_still_generic_because_the_example_leaves_it_commented(
    installed: Path,
):
    resolved = loader.resolve()
    assert resolved.profile == layers.BUILTIN_PROFILE
    assert resolved.profile_source == "builtin"


def test_the_examples_privacy_patterns_compile_and_catch_their_own_placeholder(
    installed: Path,
):
    import re

    patterns = [re.compile(p) for p in loader.privacy_patterns()]
    assert patterns
    assert any(p.search("EMP-123456") for p in patterns)


def test_the_examples_corrections_merge_over_the_generic_table(installed: Path):
    merged = loader.corrections()["deterministic"]
    # the overlay's own pair
    assert merged["複力效果"] == "複利效果"
    # and the generic profile's pairs are still there
    assert merged["Copilet"] == "Copilot"

    sources = loader.resolve().sources
    assert sources["corrections.deterministic.複力效果"] == "user"
    assert sources["corrections.deterministic.Copilet"] == "builtin"


# --------------------------------------------------------------------------
# the README tells people to do this
# --------------------------------------------------------------------------
def test_the_project_readme_documents_the_copy():
    text = (EXAMPLE.parents[1] / "README.md").read_text(encoding="utf-8")
    assert "examples/overlay-minimal" in text
    assert "~/.lecture2notes" in text
