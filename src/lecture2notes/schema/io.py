"""Reading and writing canonical lecture JSON.

Every canonical document in this package is written through
:func:`write_json_atomic`, and there is exactly one implementation of the rules
so they cannot drift per call site:

* UTF-8 with no byte order mark. A BOM makes the browser-side player fail on a
  file that looks perfectly fine in an editor.
* Two-space indent, ``ensure_ascii=False``, LF newlines. The documents are
  reviewed in diffs, so escaped Chinese and CRLF are both a real cost.
* A temporary file in the *same* directory, then :func:`os.replace`. A rename
  within one directory is atomic on both POSIX and Windows, so a reader never
  sees a half-written document and a crash mid-write leaves the previous
  version byte-for-byte intact.

The temp file is re-read and parsed before the rename, so a truncated or
non-serialisable write can never replace a good file.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Union

#: Indent used for every canonical document.
JSON_INDENT = 2
BOM = b"\xef\xbb\xbf"


def dumps(data: Any) -> str:
    """The canonical serialisation, without writing anything."""
    return json.dumps(data, ensure_ascii=False, indent=JSON_INDENT, allow_nan=False)


def write_json_atomic(path: Union[str, Path], obj: Any) -> Path:
    """Write ``obj`` to ``path`` atomically as canonical UTF-8 JSON.

    Returns the destination path. On any failure the temporary file is removed
    and the destination is left exactly as it was.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".%s." % destination.name, suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=JSON_INDENT, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Parse it back before committing: a partial write must never win.
        json.loads(Path(temp_name).read_text(encoding="utf-8"))
        os.replace(temp_name, destination)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return destination


def read_json(path: Union[str, Path]) -> Any:
    """Read a JSON file, tolerating a stray BOM on *input* only."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def has_bom(path: Union[str, Path]) -> bool:
    """Does the file start with a UTF-8 byte order mark?"""
    with open(Path(path), "rb") as handle:
        return handle.read(3) == BOM


__all__ = [
    "BOM",
    "JSON_INDENT",
    "dumps",
    "has_bom",
    "read_json",
    "write_json_atomic",
]
