"""Process exit codes shared by every subcommand.

The meaning of each code is part of the public contract; do not reuse them.
"""

OK = 0
WARN = 1
ERROR = 2
MISSING_DEPENDENCY = 3
NOT_IMPLEMENTED = 4

__all__ = ["OK", "WARN", "ERROR", "MISSING_DEPENDENCY", "NOT_IMPLEMENTED"]
