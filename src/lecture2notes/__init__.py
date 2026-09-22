"""lecture2notes: lecture recordings to structured notes, viewer and course hub."""

from importlib import metadata

#: What `l2n --version` prints when the package is not installed as a
#: distribution -- a source checkout run through ``PYTHONPATH``, which is how
#: contributors and several of this project's own verification runs execute it.
#: It must equal ``pyproject.toml``'s ``version``; a test asserts that, because
#: two numbers that are allowed to disagree eventually do.
FALLBACK_VERSION = "0.2.1"

try:
    __version__ = metadata.version("lecture2notes")
except metadata.PackageNotFoundError:  # running from a source tree
    __version__ = FALLBACK_VERSION

__all__ = ["FALLBACK_VERSION", "__version__"]
