"""Allow `python -m lecture2notes.cli` as an alias of the `l2n` console script.

`lecture2notes.cli` is a package, so `python -m lecture2notes.cli` looks for a
`__main__` module inside it and fails with "No module named
lecture2notes.cli.__main__" without one. That is a confusing error to hit while
debugging, because the shape that does work -- `python -m lecture2notes.cli.main`
-- is one dot longer and looks like a typo. Both spellings, and
`python -m lecture2notes`, now run exactly what `l2n` runs.
"""

import sys

from lecture2notes.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
