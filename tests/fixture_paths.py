"""Where the one committed recording lives, for the two tests that use it.

`tests/` is put on `sys.path` by pytest's rootdir handling (the same mechanism
`from fixtures import synthetic` already relies on), so this is imported as a
plain top-level module, not as `tests.fixture_paths`.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = REPO_ROOT / "tests" / "fixtures" / "media"

#: The only video file the repository is allowed to contain. CC BY 3.0; see
#: MEDIA_README for provenance, licence text, sha256 and transcode parameters.
FIXTURE_CLIP = MEDIA_DIR / "copyrightx-12-1-clip.mp4"
FIXTURE_README = MEDIA_DIR / "README.md"

#: What the clip actually contains, so tests can assert against it without
#: re-probing: 25 seconds, one scene cut around 10-11s (credits card to title
#: card) and a second around 15-16s (title card to speaker).
CLIP_DURATION_SEC = 25.0
CLIP_FIRST_CUT_SEC = 10.5
