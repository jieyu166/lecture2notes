"""README's acknowledgement section names the upstream repo and the required wording."""

from __future__ import annotations

from pathlib import Path

import pytest

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


# ---------------------------------------------------------------------------
# Scope, privacy boundary and the rest of the README contract (group 11).
#
# Everything below is a claim a reader acts on before running anything: what
# leaves the machine, which platform is actually supported, what the licence
# is, and how to install. A README that loses one of these sections in a merge
# is worse than one that never had it, because the omission reads as "not
# applicable" rather than "missing".
# ---------------------------------------------------------------------------

ENGINE_NAMES = ("breeze_ct2", "faster_whisper", "whisper_cpp", "qwen3_asr")

OVERLAY_FILENAMES = (
    "note.frontmatter.yaml",
    "note.template.md",
    "corrections.json",
    "outputs.toml",
    "privacy.toml",
)


def _sections(text: str) -> dict[str, str]:
    """Map each `## ` heading to its body, up to the next `## `."""
    out: dict[str, str] = {}
    current = None
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                out[current] = "\n".join(buffer)
            current = line[3:].strip()
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        out[current] = "\n".join(buffer)
    return out


def _privacy_section(text: str) -> str:
    for heading, body in _sections(text).items():
        if "隱私" in heading or "Privacy" in heading or "privacy" in heading:
            return body
    raise AssertionError("README has no heading containing 隱私 or Privacy")


def test_readme_has_a_privacy_section():
    # The assertion the spec names: the heading must exist at all.
    _privacy_section(_readme_text())


def test_privacy_section_states_local_asr_uploads_nothing():
    body = _privacy_section(_readme_text())
    assert "本機" in body and "ASR" in body
    assert "不上傳" in body or "不會送" in body or "不會上傳" in body


def test_privacy_section_names_the_model_provider_boundary():
    body = _privacy_section(_readme_text())
    assert "模型供應商" in body, (
        "the privacy section does not say that LLM expansion sends text to a "
        "model provider -- that is the step that leaves the machine"
    )


def test_readme_states_windows_first_and_best_effort_elsewhere():
    text = _readme_text()
    assert "Windows" in text
    assert "盡力支援" in text
    assert "Linux" in text and "macOS" in text


def test_readme_states_the_mit_licence():
    text = _readme_text()
    assert "MIT" in text
    assert "LICENSE" in text


@pytest.mark.parametrize("engine", ENGINE_NAMES)
def test_readme_lists_every_engine(engine):
    assert engine in _readme_text(), "%s is missing from the engine table" % engine


def test_engine_table_carries_local_and_gpu_columns():
    text = _readme_text()
    assert "local" in text
    assert "needs_gpu" in text
    assert "native_timestamps" in text


def test_readme_keeps_the_qwen_implementation_notes():
    # Written in group 4 from the upstream source; it is the one part of the
    # README that a reader cannot reconstruct from the code.
    text = _readme_text()
    assert "Qwen3-ForcedAligner-0.6B" in text
    assert "qwen-asr" in text


def test_readme_has_an_install_section_with_pip_and_extras():
    sections = _sections(_readme_text())
    install = next((body for name, body in sections.items() if "安裝" == name), None)
    assert install is not None, "README has no 安裝 section"
    assert "pip install" in install
    for extra in ("breeze", "qwen", "whispercpp", "scene", "dev"):
        assert extra in install, "extra %r is not documented" % extra


def test_readme_has_a_quick_start_that_names_the_mandatory_lang_flag():
    sections = _sections(_readme_text())
    assert "快速開始" in sections
    assert "--lang" in sections["快速開始"]


def test_readme_has_the_overlay_howto_anchor():
    # Group 10 adds its own overlay material at this anchor; the anchor is the
    # merge point, so losing it turns a clean merge into a guess.
    text = _readme_text()
    assert "<!-- overlay-howto -->" in text
    assert "## Overlay 設定" in text


def test_overlay_section_names_all_five_overridable_files():
    sections = _sections(_readme_text())
    body = sections.get("Overlay 設定")
    assert body is not None
    for filename in OVERLAY_FILENAMES:
        assert filename in body, "%s is not documented in the overlay section" % filename


def test_overlay_section_documents_the_resolution_order():
    body = _sections(_readme_text())["Overlay 設定"]
    for layer in ("CLI", ".lecture2notes/", "profiles/", "內建"):
        assert layer in body, "resolution order does not mention %r" % layer


def test_readme_documents_the_three_skill_install_targets():
    text = _readme_text()
    for path in (
        "~/.claude/skills/lecture2notes",
        "~/.agents/skills/lecture2notes",
        "~/.config/opencode/skills/lecture2notes",
    ):
        assert path in text, "install target %s is not documented" % path


def test_readme_uses_the_lecture2notes_skill_name_not_the_upstream_one():
    text = _readme_text()
    # The upstream repo URL legitimately contains "lecture-to-notes"; nothing
    # else may, or the installer and the README disagree about the directory.
    without_upstream_url = text.replace(UPSTREAM_URL, "")
    assert "skills/lecture-to-notes" not in without_upstream_url


def test_readme_console_markers_stay_cp950_safe():
    forbidden = set("→≥≤✓✗✔✘")
    found = forbidden & set(_readme_text())
    assert not found, "README contains cp950-unsafe symbol(s) %s" % sorted(found)
