"""The two built-in profiles: what generic promises, and what radiology adds.

The provenance tests here are the point of the file. The correction tables in
this package were rebuilt from scratch precisely so that nothing derived from a
private vault ships in a public MIT repository, and "we were careful once" is
not a guarantee -- a test that reads the shipped files every run is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lecture2notes.cli.main import main
from lecture2notes.engines import corrections as corrections_mod
from lecture2notes.notes import guideline
from lecture2notes.profiles import layers, loader

PROFILES = ("generic", "radiology")

#: Any of these in a correction table's provenance means the entry came from
#: the private vault this package was extracted from.
FORBIDDEN_SOURCES = ("zerotype", "user.md")


@pytest.fixture
def clean_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No overlays anywhere, so the shipped profiles answer for themselves."""
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(work)
    return home


def table(profile: str) -> dict:
    path = layers.profile_dir(profile) / layers.CORRECTIONS_FILE
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# both profiles ship, and generic is the default
# --------------------------------------------------------------------------
def test_both_builtin_profiles_ship():
    assert set(PROFILES) <= set(layers.builtin_profiles())


def test_generic_is_the_default_and_says_so_is_builtin(clean_home: Path):
    resolved = loader.resolve()
    assert resolved.profile == "generic"
    assert resolved.profile_source == "builtin"
    assert resolved.settings()["profile"].source == "builtin"


def test_profile_show_reports_generic_from_the_builtin_layer(clean_home: Path, capsys):
    assert main(["profile", "show", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["profile"] == {"value": "generic", "source": "builtin"}


def test_radiology_is_not_active_unless_it_is_selected(clean_home: Path):
    """The radiology template must not reach a note nobody asked it to."""
    assert "reading-case" not in (loader.note_template() or "")
    assert "reading-case" in (loader.note_template("radiology") or "")


# --------------------------------------------------------------------------
# what generic promises
# --------------------------------------------------------------------------
def test_generic_settings(clean_home: Path):
    settings = loader.resolve().settings()
    assert settings["note.style"].value == "concise"
    assert settings["pbf"].value is False
    assert settings["hub"].value is True
    assert loader.note_style() == "concise"


def test_generic_frontmatter_has_exactly_four_fields(clean_home: Path):
    text = loader.frontmatter_template() or ""
    fields = [
        line.split(":", 1)[0].strip()
        for line in text.splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and not line.strip().startswith("---")
    ]
    assert fields == ["title", "date", "source", "tags"]


def test_generic_privacy_ships_no_name_list(clean_home: Path):
    # A public default that guesses at personal names false-positives on
    # ordinary words. Every shipped pattern must compile and must be an
    # identifier by construction, not a literal name.
    patterns = loader.privacy_patterns()
    assert patterns
    for pattern in patterns:
        re.compile(pattern)


# --------------------------------------------------------------------------
# what radiology adds
# --------------------------------------------------------------------------
def test_radiology_template_carries_a_reading_callout():
    text = (layers.profile_dir("radiology") / layers.NOTE_TEMPLATE).read_text(
        encoding="utf-8"
    )
    assert "> [!reading-case]" in text


def test_radiology_template_keeps_the_mandatory_sections_in_order():
    text = (layers.profile_dir("radiology") / layers.NOTE_TEMPLATE).read_text(
        encoding="utf-8"
    )
    lines = text.splitlines()
    positions = []
    for heading, name in guideline.MANDATORY_SECTIONS:
        assert heading in lines, "radiology template is missing %s" % heading
        positions.append(lines.index(heading))
    assert positions == sorted(positions)


def test_radiology_privacy_adds_patient_identifiers_without_losing_the_generic_ones(
    clean_home: Path,
):
    patterns = loader.privacy_patterns("radiology")
    joined = "\n".join(patterns)
    # its own
    assert any(r"\d{8}(?!\d)" in p for p in patterns)
    assert any("病患" in p for p in patterns)
    assert any(r"[A-Za-z][12]\d{8}" in p for p in patterns)
    # and the generic ones it restates, because one key replaces one key
    assert "@" in joined

    compiled = [re.compile(p) for p in patterns]
    for text in ("病患：王小明", "A123456789", "陳先生的病歷號 12345678"):
        assert any(c.search(text) for c in compiled), text
    # An ordinary sentence of a lecture note is not an identifier.
    for text in ("左側腎臟可見水腎", "日期 2026-09-21"):
        assert not any(c.search(text) for c in compiled), text
    # Neither is a yyyymmdd date. A lecture stem is routinely one, and
    # `l2n render` writes it into the frontmatter, the References block and
    # every frame embed, so every one of those findings was a false positive.
    for text in (
        'source: ["20230215.mp4"]',
        "![[frames/20230215-0000.png]]",
        "- subtitle: 20230215.srt",
        "20230215 那一場",
    ):
        assert not any(c.search(text) for c in compiled), text
    # A date-shaped run is excused; a malformed one is still an identifier.
    for text in ("20231345", "19991301"):
        assert any(c.search(text) for c in compiled), text


def test_radiology_does_not_override_settings_it_has_no_opinion_about(
    clean_home: Path,
):
    # It ships no outputs.toml, so a layer that lacks a file contributes
    # nothing and the generic answers stand.
    settings = loader.resolve("radiology").settings()
    assert settings["pbf"].value is False
    assert settings["pbf"].source == "builtin"
    assert settings["note.style"].source == "builtin"
    # ...but the template it does ship wins.
    assert settings["template." + layers.NOTE_TEMPLATE].source == "profile"


# --------------------------------------------------------------------------
# provenance: nothing here came from the private vault
# --------------------------------------------------------------------------
@pytest.mark.parametrize("profile", PROFILES)
def test_no_shipped_correction_entry_cites_a_private_source(profile: str):
    raw = (layers.profile_dir(profile) / layers.CORRECTIONS_FILE).read_text(
        encoding="utf-8"
    )
    lowered = raw.lower()
    for forbidden in FORBIDDEN_SOURCES:
        assert forbidden not in lowered, (
            "%s/corrections.json mentions %r" % (profile, forbidden)
        )


@pytest.mark.parametrize("profile", PROFILES)
def test_every_shipped_entry_declares_this_project_as_its_source(profile: str):
    entries = loader.Resolved(
        profile=profile, profile_source="profile", corrections=table(profile)
    ).correction_entries()
    assert entries
    for entry in entries:
        assert entry["source"] == "lecture2notes-%s" % profile, entry


def test_generic_holds_general_vocabulary_and_radiology_holds_terminology():
    generic = loader.Resolved(
        profile="generic", profile_source="profile", corrections=table("generic")
    ).correction_entries()
    radiology = loader.Resolved(
        profile="radiology", profile_source="profile", corrections=table("radiology")
    ).correction_entries()

    corrected = {e["correct"] for e in generic}
    assert {"Copilot", "Markdown", "Claude", "GitHub"} <= corrected

    corrected = {e["correct"] for e in radiology}
    assert {"Lung-RADS", "BI-RADS", "tomosynthesis", "顯影劑"} <= corrected


def test_the_auto_applied_sections_are_the_ones_the_engine_applies():
    """A table whose section the engine does not know about is never applied."""
    for profile in PROFILES:
        sections = [
            key
            for key in table(profile)
            if not key.startswith(layers.META_PREFIX) and key != "context_sensitive"
        ]
        assert sections, profile
        for section in sections:
            assert section in corrections_mod.AUTO_SECTIONS, (profile, section)


def test_context_sensitive_pairs_are_never_applied_automatically():
    for profile in PROFILES:
        pairs = corrections_mod.load_table(table(profile))
        heard = {pair[0] for pair in pairs}
        for key in table(profile).get("context_sensitive", {}):
            assert key not in heard, (profile, key)
