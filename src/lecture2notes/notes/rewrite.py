"""Guards a content rewrite must pass, and the privacy scan that gates it.

Ported from rad-workflow
.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/rewrite_lecture.py
and the reusable pure functions of rewrite_evidence.py.

Only the reusable half is here. The evidence-packet and review-record system in
the source, with its paired digests over claims and attestations, is specific to
one workflow and is deliberately not ported: this package's contract is the
writing guideline plus ``check note``, not a signed review chain.

What is kept is the part any rewrite needs:

* the times and the formal frames must come out of a rewrite unchanged, because
  a content pass that moves a timecode breaks the viewer, the chapters and the
  note all at once;
* a document containing personal data must not be handed to a remote model.
"""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from typing import Any, List, Mapping, Sequence

from lecture2notes.schema.model import assert_times_unchanged

#: Refuse to scan more than this; a scan that never finishes is not a safeguard.
MAX_SCAN_CHARS = 2_000_000

SENSITIVE_PATTERNS = (
    (
        "medical_record_number",
        re.compile(
            r"(?:病\s*歷\s*(?:號碼|號)|m\s*r\s*n)\s*[:#-]?\s*"
            r"(?=[a-z0-9-]{0,16}\d)[a-z0-9](?:[a-z0-9-]{4,30}[a-z0-9])",
            re.IGNORECASE,
        ),
    ),
    (
        "patient_name",
        re.compile(r"(?:病\s*人\s*)?姓\s*名\s*[:：-]*\s*[㐀-鿿](?:\s*[㐀-鿿]){1,3}"),
    ),
    (
        "birth_date",
        re.compile(
            r"(?:生\s*日|出\s*生(?:\s*日\s*期)?)\s*[:：-]*\s*"
            r"(?:(?:19|20)\d{2}|\d{2,3})\s*[/.年-]\s*\d{1,2}\s*[/.月-]\s*\d{1,2}(?:\s*日)?"
        ),
    ),
    (
        "phone",
        re.compile(r"(?<!\d)(?:\+?886\s*[- ]?\s*9|0\s*9)\s*\d\s*\d(?:\s*[- ]?\s*\d){6}(?!\d)"),
    ),
    (
        "national_id",
        re.compile(r"(?<![a-z0-9])[a-z]\s*[12](?:\s*[- ]?\s*\d){8}(?!\d)", re.IGNORECASE),
    ),
    (
        "email",
        re.compile(r"[a-z0-9._%+-]+\s*@\s*[a-z0-9.-]+\s*\.\s*[a-z]{2,}", re.IGNORECASE),
    ),
    (
        "identifier",
        re.compile(
            r"(?:病\s*人\s*識\s*別\s*碼|病\s*患\s*識\s*別\s*碼|patient\s*id|identifier|accession)"
            r"\s*[:：#-]*\s*[a-z0-9](?:[a-z0-9 -]{3,40}[a-z0-9])",
            re.IGNORECASE,
        ),
    ),
)


class RewriteError(ValueError):
    """A rewritten document cannot safely replace its source."""


def normalize_text(value: str) -> str:
    """NFKC, LF line endings, no invisible formatting characters."""
    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        character for character in normalized if unicodedata.category(character) != "Cf"
    )


def security_view(text: str) -> str:
    """Flatten the separators an identifier can be disguised with.

    Every dash-like character and every slash becomes ``-``; other punctuation
    becomes a space. Without this, a phone number split by an unusual dash slips
    past a pattern that would otherwise match it.
    """
    normalized = normalize_text(text)
    result: List[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category == "Pd" or character in {"/", "\\"}:
            result.append("-")
        elif character in {"@", ".", "+", "-"}:
            result.append(character)
        elif category.startswith("P"):
            result.append(" ")
        else:
            result.append(character)
    return "".join(result)


def contains_sensitive_data(text: str) -> List[str]:
    """Names of the personal-data patterns present in ``text``.

    The text is checked twice: once with separators flattened, and once with all
    whitespace removed, because an identifier broken across a line wrap is still
    an identifier. The record-number pattern is exempt from the whitespace-free
    pass, where it produces false positives.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if len(text) > MAX_SCAN_CHARS:
        raise ValueError("text exceeds maximum length")
    normalized = security_view(text)
    compact = re.sub(r"\s+", "", normalized)
    return [
        name
        for name, pattern in SENSITIVE_PATTERNS
        if pattern.search(normalized)
        or (name != "medical_record_number" and pattern.search(compact))
    ]


def strict_json(value: Any) -> str:
    """Canonical JSON form used to compare two structures exactly."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def assert_frames_unchanged(
    source_segments: Sequence[Mapping[str, Any]],
    candidate_segments: Sequence[Mapping[str, Any]],
) -> None:
    """Raise unless every segment's frame list survived the rewrite intact."""
    if len(source_segments) != len(candidate_segments):
        raise RewriteError("frames_changed:segment_count")
    for position, (source, candidate) in enumerate(zip(source_segments, candidate_segments)):
        source_frames = source.get("frames")
        candidate_frames = candidate.get("frames")
        if not isinstance(source_frames, list) or not isinstance(candidate_frames, list):
            raise RewriteError("frames_changed:segment=%d" % position)
        if strict_json(source_frames) != strict_json(candidate_frames):
            raise RewriteError("frames_changed:segment=%d" % position)


def segments_of(document: Mapping[str, Any], label: str) -> List[Mapping[str, Any]]:
    segments = document.get("segments")
    if (
        not isinstance(segments, list)
        or not segments
        or not all(isinstance(item, Mapping) for item in segments)
    ):
        raise RewriteError("%s_segments_invalid" % label)
    return segments


def accept_rewrite(
    source: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict:
    """Return a copy of ``candidate`` once it has passed every structural guard.

    Content is free to change; structure is not.
    """
    if not isinstance(source, Mapping) or not isinstance(candidate, Mapping):
        raise RewriteError("document_invalid")
    source_segments = segments_of(source, "source")
    candidate_segments = segments_of(candidate, "candidate")
    try:
        assert_times_unchanged(source, candidate)
    except (TypeError, ValueError) as error:
        raise RewriteError("time_changed:%s" % error) from error
    assert_frames_unchanged(source_segments, candidate_segments)
    return copy.deepcopy(dict(candidate))


def remote_rewrite_allowed(text: str) -> bool:
    """False when the text carries personal data, so it must stay local."""
    return not contains_sensitive_data(text)


__all__ = [
    "MAX_SCAN_CHARS",
    "RewriteError",
    "SENSITIVE_PATTERNS",
    "accept_rewrite",
    "assert_frames_unchanged",
    "contains_sensitive_data",
    "normalize_text",
    "remote_rewrite_allowed",
    "security_view",
    "segments_of",
    "strict_json",
]
