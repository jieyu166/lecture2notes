"""The one video file in the repository stays the one we documented.

`tests/fixtures/media/copyrightx-12-1-clip.mp4` is the only media file
`.gitignore` lets into the repository, and the end-to-end test runs on it. Two
things about it have to keep being true, and neither is visible in a diff:

1. The bytes are the bytes whose provenance and licence `README.md` records.
   A fixture swapped for "a better clip" with the README left alone would
   redistribute someone else's recording under a licence statement that was
   never about it.
2. The licence is a *permissive* Creative Commons one. `CC BY-NC` or `CC BY-ND`
   material cannot ship in an MIT-licensed repository that people redistribute,
   so the licence section is read, not trusted.

These checks need no model, no network and no ffmpeg (the duration check skips
itself when ffprobe is absent), so they run in ordinary CI.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess

import pytest

from fixture_paths import FIXTURE_CLIP, FIXTURE_README

#: The clip is a fixture, not a payload: if it ever grows past this it is no
#: longer "one small file in the repository" and wants a download step instead.
MAX_BYTES = 3 * 1024 * 1024

EXPECTED_DURATION_SEC = 25.0
DURATION_TOLERANCE_SEC = 1.0

#: `sha256sum`-style line in the README: `` `<64 hex>` `` after "Cropped file".
CROPPED_SHA_RE = re.compile(
    r"Cropped file SHA-256:\s*`(?P<sha>[0-9a-f]{64})`", re.I
)

#: Licence words that would make the clip undistributable here. Word-bounded on
#: purpose: a bare "NC" substring also matches ordinary prose such as "AND".
FORBIDDEN_LICENCE_RE = re.compile(
    r"\bBY-NC\b|\bBY-ND\b|\bNC\b|\bND\b|NonCommercial|NoDeriv", re.I
)


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture(scope="module")
def readme_text() -> str:
    assert FIXTURE_README.is_file(), "%s is missing" % FIXTURE_README
    return FIXTURE_README.read_text(encoding="utf-8")


def test_fixture_clip_exists_and_is_small():
    assert FIXTURE_CLIP.is_file(), (
        "the end-to-end fixture %s is missing; .gitignore names it explicitly, "
        "so a clone that does not have it means it was never committed"
        % FIXTURE_CLIP
    )
    size = FIXTURE_CLIP.stat().st_size
    assert 0 < size <= MAX_BYTES, (
        "fixture clip is %d bytes, over the %d-byte ceiling" % (size, MAX_BYTES)
    )


def test_fixture_clip_matches_the_sha256_the_readme_records(readme_text):
    match = CROPPED_SHA_RE.search(readme_text)
    assert match, "README.md records no 'Cropped file SHA-256' for the clip"
    assert _sha256(FIXTURE_CLIP) == match.group("sha").lower(), (
        "the clip's bytes do not match the sha256 in "
        "tests/fixtures/media/README.md: either the file was replaced without "
        "updating its provenance record, or the record is wrong"
    )


def test_readme_records_a_permissive_creative_commons_licence(readme_text):
    licence_section = readme_text.split("## License", 1)
    assert len(licence_section) == 2, "README.md has no '## License' section"
    body = licence_section[1].split("\n## ", 1)[0]

    assert "CC BY 3.0" in body or "Creative Commons Attribution 3.0" in body, (
        "the README's licence section does not state CC BY 3.0"
    )
    forbidden = FORBIDDEN_LICENCE_RE.search(body)
    assert not forbidden, (
        "the README's licence section mentions %r: a NonCommercial or "
        "NoDerivatives clip cannot be redistributed from this repository"
        % forbidden.group(0)
    )


def test_readme_links_the_source_and_names_the_author(readme_text):
    # CC BY is satisfied by attribution, not by the licence name alone.
    assert "https://" in readme_text
    assert "William Fisher" in readme_text
    assert "creativecommons.org/licenses/by/3.0" in readme_text


def test_notice_carries_the_same_media_attribution():
    from fixture_paths import REPO_ROOT

    notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
    assert FIXTURE_CLIP.name in notice, (
        "NOTICE does not mention the bundled clip; someone reading only the "
        "root licence files would not learn a CC BY file is in the tree"
    )
    assert "CC BY 3.0" in notice
    assert "William Fisher" in notice


def test_media_directory_holds_only_the_clip_and_its_readme():
    names = sorted(p.name for p in FIXTURE_CLIP.parent.iterdir() if p.is_file())
    assert names == sorted([FIXTURE_CLIP.name, FIXTURE_README.name]), (
        "tests/fixtures/media/ should hold exactly the one clip and its "
        "README; found %s" % names
    )


def test_clip_is_twenty_five_seconds_long():
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe is not on PATH; cannot measure the clip")
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-show_entries", "stream=codec_type", "-of", "json",
            str(FIXTURE_CLIP),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, "ffprobe failed: %s" % result.stderr
    probed = json.loads(result.stdout)
    duration = float(probed["format"]["duration"])
    assert abs(duration - EXPECTED_DURATION_SEC) <= DURATION_TOLERANCE_SEC, (
        "clip is %.3fs, expected %.1f +/- %.1f"
        % (duration, EXPECTED_DURATION_SEC, DURATION_TOLERANCE_SEC)
    )
    kinds = {stream["codec_type"] for stream in probed.get("streams", [])}
    assert {"video", "audio"} <= kinds, (
        "the clip needs both a video and an audio stream to exercise the "
        "pipeline; ffprobe reports %s" % sorted(kinds)
    )
