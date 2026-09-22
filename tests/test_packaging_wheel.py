"""Build a real wheel, install it into a clean venv, and use it.

Everything else in this suite runs against a source checkout, where the three
resources authored outside `src/` are sitting right there at their repository
paths. That is precisely why v0.2.0 shipped a wheel that could not find them
and no test said a word. A unit test cannot catch this class of defect: the
defect is in what the build puts in the archive, so the only test that means
anything builds the archive and installs it somewhere the repository is not
reachable.

The run is slow (a wheel build plus a venv plus a pip install, tens of
seconds) and needs the network for pip's build isolation, so it is marked
`packaging` and deselected from the default run by `tests/conftest.py`. CI
gives it a job of its own.

Four claims, one per thing v0.2.0 got wrong, plus the version:

* `l2n install-skill --all` fills all three targets and `--check` exits 0;
* `l2n render --expand-prompt` quotes the rules rather than naming the file;
* `l2n profile init` writes the overlay example;
* the wheel's own file list carries the skill, the guideline and the example.

The venv is built in pytest's `tmp_path` and HOME is redirected into it, so a
run cannot reach the developer's real `~/.claude`, `~/.agents`,
`~/.config/opencode` or `~/.lecture2notes`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from lecture2notes import install as install_mod
from lecture2notes.notes import guideline
from lecture2notes.profiles import bootstrap, layers

pytestmark = pytest.mark.packaging

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Where the wheel's copies have to land for `lecture2notes.resources` to find
#: them. Spelled out rather than derived from pyproject, so a typo in the
#: force-include table fails here instead of being read back as its own answer.
EXPECTED_IN_WHEEL = (
    "lecture2notes/_bundled/skill/SKILL.md",
    "lecture2notes/_bundled/skill/references/frames-and-notes.md",
    "lecture2notes/_bundled/skill/references/note-writing.md",
    "lecture2notes/_bundled/skill/references/outputs-and-batch.md",
    "lecture2notes/_bundled/skill/references/profiles-and-overlay.md",
    "lecture2notes/_bundled/skill/references/segmentation.md",
    "lecture2notes/_bundled/skill/references/transcription.md",
    "lecture2notes/_bundled/docs/note-writing-guideline.md",
    "lecture2notes/_bundled/examples/overlay-minimal/note.frontmatter.yaml",
    "lecture2notes/_bundled/examples/overlay-minimal/note.template.md",
    "lecture2notes/_bundled/examples/overlay-minimal/corrections.json",
    "lecture2notes/_bundled/examples/overlay-minimal/outputs.toml",
    "lecture2notes/_bundled/examples/overlay-minimal/privacy.toml",
)


def _scripts_dir(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def _venv_python(venv: Path) -> Path:
    name = "python.exe" if os.name == "nt" else "python"
    return _scripts_dir(venv) / name


def _l2n(venv: Path) -> Path:
    name = "l2n.exe" if os.name == "nt" else "l2n"
    return _scripts_dir(venv) / name


def _run(
    command: List[str], cwd: Path, env: Optional[Dict[str, str]] = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A wheel built from this checkout by the declared build backend."""
    out = tmp_path_factory.mktemp("wheelhouse")
    result = _run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)], REPO_ROOT
    )
    if result.returncode != 0:
        # `build` is a dev-time tool this project does not depend on. Saying so
        # is more useful than a traceback about a missing module.
        if "No module named build" in (result.stderr or ""):
            pytest.skip("the `build` package is not installed in this interpreter")
        pytest.fail(
            "wheel build failed (%s)\n%s\n%s"
            % (result.returncode, result.stdout, result.stderr)
        )
    wheels = sorted(out.glob("lecture2notes-*.whl"))
    assert len(wheels) == 1, [p.name for p in wheels]
    return wheels[0]


@pytest.fixture(scope="module")
def installed(
    wheel: Path, tmp_path_factory: pytest.TempPathFactory
) -> Dict[str, Path]:
    """The wheel installed into a venv, with HOME redirected into tmp.

    `--no-deps` is deliberate: this asks whether the archive carries the data
    files, and resolving PyYAML from the network is a different question that
    can fail for its own reasons. PyYAML is installed separately so the CLI
    imports; if that fails the test skips rather than reporting a packaging
    defect that is really a network outage.
    """
    root = tmp_path_factory.mktemp("wheel-install")
    venv = root / "venv"
    home = root / "home"
    home.mkdir()
    work = root / "work"
    work.mkdir()

    made = _run([sys.executable, "-m", "venv", str(venv)], root)
    assert made.returncode == 0, made.stderr

    python = _venv_python(venv)
    assert python.is_file(), python

    step = _run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)], root)
    assert step.returncode == 0, step.stdout + step.stderr

    deps = _run([str(python), "-m", "pip", "install", "PyYAML>=6.0"], root)
    if deps.returncode != 0:
        pytest.skip("cannot install PyYAML into the throwaway venv (offline?)")

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONIOENCODING"] = "utf-8"
    # Nothing from the developer's environment may leak in: a stale PYTHONPATH
    # pointing at `src/` would let every check below pass on a wheel that
    # carries none of these files.
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)

    return {"venv": venv, "home": home, "work": work, "python": python, "env": env}


def _cli(installed: Dict[str, Path], *args: str) -> subprocess.CompletedProcess:
    return _run(
        [str(_l2n(installed["venv"]))] + list(args),
        installed["work"],
        env=installed["env"],
    )


# --------------------------------------------------------------------------
# the archive
# --------------------------------------------------------------------------
def test_the_wheel_carries_the_skill_the_guideline_and_the_example(wheel: Path):
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    missing = [name for name in EXPECTED_IN_WHEEL if name not in names]
    assert missing == [], missing


def test_the_wheel_does_not_ship_the_tests_or_the_repository_root(wheel: Path):
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()

    assert not [n for n in names if n.startswith("tests/")]
    assert not [n for n in names if n.startswith("skill/")]
    assert not [n for n in names if n.startswith("docs/")]


# --------------------------------------------------------------------------
# the installed command
# --------------------------------------------------------------------------
def test_the_installed_cli_reports_the_declared_version(installed: Dict[str, Path]):
    import lecture2notes

    result = _cli(installed, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "lecture2notes %s" % lecture2notes.FALLBACK_VERSION


def test_the_package_cannot_see_this_checkout(installed: Dict[str, Path]):
    """The premise of every check below: no repository is reachable."""
    probe = (
        "import json,sys;from lecture2notes import resources;"
        "print(json.dumps({'repo': str(resources.repo_root()),"
        " 'bundle': str(resources.bundle_root())}))"
    )
    result = _run(
        [str(installed["python"]), "-c", probe],
        installed["work"],
        env=installed["env"],
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["repo"] == "None"
    assert payload["bundle"].endswith("_bundled")


def test_install_skill_fills_all_three_targets(installed: Dict[str, Path]):
    result = _cli(installed, "install-skill", "--all")
    assert result.returncode == 0, result.stdout + result.stderr

    home = installed["home"]
    for name in install_mod.TARGET_NAMES:
        target = home.joinpath(*install_mod.TARGET_RELATIVE_PATHS[name])
        assert (target / "SKILL.md").is_file(), target
        references = sorted(p.name for p in (target / "references").glob("*.md"))
        assert len(references) == 6, references
        record = json.loads((target / install_mod.RECORD_NAME).read_text("utf-8"))
        assert record["target"] == name
        assert len(record["source_sha256"]) == 64


def test_install_skill_check_exits_zero_right_after_installing(
    installed: Dict[str, Path],
):
    assert _cli(installed, "install-skill", "--all").returncode == 0

    result = _cli(installed, "install-skill", "--check", "--all")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("[ok]") == len(install_mod.TARGET_NAMES)
    assert "[drift]" not in result.stdout


def test_the_wheels_skill_hash_equals_this_checkouts(installed: Dict[str, Path]):
    """The bundled copy is the repository's files, not a re-rendered version.

    If these differed, `--check` would still pass -- it compares the target
    against whatever the package shipped -- and the skill people install would
    quietly not be the skill in git.
    """
    assert _cli(installed, "install-skill", "--all").returncode == 0

    target = installed["home"].joinpath(
        *install_mod.TARGET_RELATIVE_PATHS["claude"]
    )
    names = install_mod.iter_source_files(REPO_ROOT / "skill")
    assert install_mod.content_hash(REPO_ROOT / "skill", names) == (
        install_mod.content_hash(target, names)
    )


def test_install_skill_reports_drift_after_an_edit(installed: Dict[str, Path]):
    assert _cli(installed, "install-skill", "--target", "claude").returncode == 0
    target = installed["home"].joinpath(
        *install_mod.TARGET_RELATIVE_PATHS["claude"]
    )
    edited = target / "references" / "segmentation.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8")

    result = _cli(installed, "install-skill", "--check", "--target", "claude")
    assert result.returncode != 0
    assert "references/segmentation.md: differs" in result.stdout


def test_expand_prompt_quotes_the_rules_not_just_the_path(
    installed: Dict[str, Path],
):
    document = installed["work"] / "20240115 packaging probe.json"
    document.write_text(
        (REPO_ROOT / "tests" / "fixtures" / "note_doc.json").read_text("utf-8"),
        encoding="utf-8",
    )

    result = _cli(installed, "render", "--expand-prompt", str(document))
    assert result.returncode == 0, result.stdout + result.stderr
    assert guideline.VERSION_LINE in result.stdout
    assert guideline.MUST_FOLLOW_HEADING in result.stdout
    # The path alone is what v0.2.0 printed when the document was missing.
    assert result.stdout.count(guideline.MUST_FOLLOW_HEADING) >= 1


def test_profile_init_writes_the_overlay_example(installed: Dict[str, Path]):
    result = _cli(installed, "profile", "init")
    assert result.returncode == 0, result.stdout + result.stderr

    target = installed["home"] / layers.OVERLAY_DIR
    for name in layers.OVERLAY_FILES:
        written = target / name
        assert written.is_file(), name
        assert written.read_bytes() == (
            REPO_ROOT / "examples" / "overlay-minimal" / name
        ).read_bytes()
    assert (target / bootstrap.EXAMPLE_README).is_file()


def test_profile_init_is_safe_to_run_twice(installed: Dict[str, Path]):
    assert _cli(installed, "profile", "init").returncode == 0
    mine = installed["home"] / layers.OVERLAY_DIR / layers.NOTE_TEMPLATE
    mine.write_text("# mine\n", encoding="utf-8")

    result = _cli(installed, "profile", "init")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "kept existing file: %s" % layers.NOTE_TEMPLATE in result.stdout
    assert mine.read_text(encoding="utf-8") == "# mine\n"


def test_profile_show_reads_the_overlay_the_init_wrote(installed: Dict[str, Path]):
    assert _cli(installed, "profile", "init").returncode == 0

    result = _cli(installed, "profile", "show", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["note.style"]["source"] == "user"


def test_the_platform_tag_is_the_pure_python_one(wheel: Path):
    """A data-only change must not turn this into a platform wheel."""
    assert wheel.name.endswith("-py3-none-any.whl"), wheel.name
    assert sysconfig.get_platform() not in wheel.name
