"""Allow `python -m lecture2notes` as an alias of the `l2n` console script."""

import sys

from lecture2notes.cli import main

if __name__ == "__main__":
    sys.exit(main())
