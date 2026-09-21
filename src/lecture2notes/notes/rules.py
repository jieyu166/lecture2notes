"""Machine-checkable content rules for one segment.

Ported from rad-workflow
.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/lecture_content_rules.py

These are the rules a check can decide on its own: length bands, distinctness,
unfinished template markers, script variant, and verbatim transcript copying. Any
rule that needs judgement stays in the writing guideline and out of this file.

The transcript-copy rule is the one that earns its keep. A model told not to
paste the transcript into the note will still do it, and the result reads fine
while being worthless, so it is measured rather than trusted: text is compared
after stripping case, width and punctuation, because that is the level at which
"copied" is actually true.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any, List, Mapping, Optional

from lecture2notes.schema.model import Finding

MIN_SUMMARY_HAN = 250
MAX_SUMMARY_HAN = 600
MAX_TITLE_CHARS = 120
MAX_TAKEAWAY_HAN = 80
#: A transcript line shorter than this carries too little signal to call a copy.
MIN_COPY_LINE_HAN = 20
#: More than this fraction of transcript lines appearing verbatim is a copy.
MAX_COPIED_LINE_RATIO = 0.5

GENERIC_TITLE = re.compile(
    r"^(?:focused|overview|summary|chapter\s*\d+|重點|介紹|病例|第[一二三四五六七八九十百0-9]+章)$",
    re.IGNORECASE,
)
# Spelled out character by character so an obfuscated marker (T-O-D-O, T.B.D)
# is caught too.
UNFINISHED_LATIN = re.compile(
    r"(?:T[\W_]*O[\W_]*D[\W_]*O|T[\W_]*B[\W_]*D|F[\W_]*I[\W_]*X[\W_]*M[\W_]*E|"
    r"P[\W_]*L[\W_]*A[\W_]*C[\W_]*E[\W_]*H[\W_]*O[\W_]*L[\W_]*D[\W_]*E[\W_]*R)",
    re.IGNORECASE,
)
UNFINISHED_ZH = (
    "請補充",
    "待確認",
    "此處填入",
    "內容待補",
    "尚未完成",
)
#: Words a Simplified-to-Traditional converter would rewrite although the
#: Traditional form is already correct in Taiwanese usage.
TAIWAN_CONTEXT_TERMS = ("干擾",)


def normalize_text(value: str) -> str:
    """NFC, LF line endings, and no invisible formatting characters."""
    normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        character for character in normalized if unicodedata.category(character) != "Cf"
    )


def han_count(text: str) -> int:
    """How many Han characters, which is the only useful length unit here."""
    return sum(
        "㐀" <= character <= "䶿"
        or "一" <= character <= "鿿"
        or "豈" <= character <= "﫿"
        for character in text
    )


def comparison_text(text: str) -> str:
    """Case-folded, width-folded, alphanumeric-only view used for copy detection."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in normalized if character.isalnum())


@lru_cache(maxsize=1)
def _converter():
    from opencc import OpenCC  # type: ignore

    failures: List[BaseException] = []
    for configuration in ("s2tw.json", "s2tw"):
        try:
            return OpenCC(configuration)
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append(exc)
    raise RuntimeError("OpenCC Simplified-to-Traditional converter is unavailable") from failures[-1]


def simplified_finding(text: str) -> Optional[Finding]:
    """Flag Simplified-only characters, or say the check could not run.

    Reporting "unavailable" as an error rather than passing silently is
    deliberate: a check that quietly does nothing is worse than no check.
    """
    try:
        converter = _converter()
    except (ImportError, OSError, RuntimeError, ValueError):
        return Finding(
            "error", "opencc_unavailable",
            "OpenCC Simplified-to-Traditional validation is unavailable",
        )
    try:
        normalized = normalize_text(text)
        protected = normalized
        for index, term in enumerate(TAIWAN_CONTEXT_TERMS):
            protected = protected.replace(term, "%d" % index)
        if converter.convert(protected) != protected:
            return Finding(
                "error", "simplified_chinese",
                "content contains Simplified Chinese-only characters",
            )
    except Exception:
        return Finding(
            "error", "opencc_failure",
            "OpenCC Simplified-to-Traditional validation failed",
        )
    return None


def _append(findings: List[Finding], code: str, message: str) -> None:
    findings.append(Finding("error", code, message))


def validate_segment_content(
    segment: Mapping[str, Any],
    transcript_text: str = "",
    required_takeaways: int = 4,
) -> List[Finding]:
    """Every content rule this module can decide, for one segment."""
    findings: List[Finding] = []
    if not isinstance(segment, Mapping):
        return [Finding("error", "segment_type", "segment must be an object")]
    if not isinstance(transcript_text, str):
        _append(findings, "transcript_type", "transcript_text must be a string")
        transcript_text = ""

    title_value = segment.get("title")
    title = normalize_text(title_value).strip() if isinstance(title_value, str) else ""
    compact_title = re.sub(r"\s+", " ", title)
    if (
        not isinstance(title_value, str)
        or not title
        or len(title) > MAX_TITLE_CHARS
        or "\n" in title
        or title.isdigit()
        or GENERIC_TITLE.fullmatch(compact_title)
    ):
        _append(findings, "title_focus", "title must name one specific subject")

    summary_value = segment.get("summary_zh")
    if not isinstance(summary_value, str):
        _append(findings, "summary_type", "summary_zh must be a string")
        summary = ""
    else:
        summary = normalize_text(summary_value).strip()
        if not MIN_SUMMARY_HAN <= han_count(summary) <= MAX_SUMMARY_HAN:
            _append(
                findings, "summary_length",
                "summary_zh must contain %d to %d Han characters"
                % (MIN_SUMMARY_HAN, MAX_SUMMARY_HAN),
            )
        paragraphs = [part for part in re.split(r"\n[^\S\n]*\n", summary) if part.strip()]
        if not 1 <= len(paragraphs) <= 2:
            _append(findings, "summary_paragraphs", "summary_zh must contain one or two paragraphs")

    takeaways_value = segment.get("takeaways_zh")
    takeaways: List[str] = []
    if not isinstance(takeaways_value, list):
        _append(findings, "takeaway_count",
                "takeaways_zh must contain exactly %d items" % required_takeaways)
    else:
        if len(takeaways_value) != required_takeaways:
            _append(findings, "takeaway_count",
                    "takeaways_zh must contain exactly %d items" % required_takeaways)
        for value in takeaways_value:
            if not isinstance(value, str):
                _append(findings, "takeaway_type", "every takeaway must be a string")
                continue
            item = normalize_text(value).strip()
            takeaways.append(item)
            if not item:
                _append(findings, "takeaway_empty", "takeaways must not be empty")
            elif han_count(item) > MAX_TAKEAWAY_HAN:
                _append(findings, "takeaway_length", "takeaways must be concise")
        comparable = [comparison_text(item) for item in takeaways if item]
        if len(comparable) != len(set(comparable)):
            _append(findings, "takeaway_duplicate", "takeaways must be distinct")

    editorial_value = segment.get("editorial_notes_zh")
    editorial: List[str] = []
    if not isinstance(editorial_value, list) or any(
        not isinstance(value, str) or not value.strip() for value in editorial_value
    ):
        _append(findings, "editorial_type",
                "editorial_notes_zh must be a list of non-empty strings")
    else:
        editorial = [normalize_text(value).strip() for value in editorial_value]

    combined = "\n".join([title, summary, *takeaways, *editorial])
    if UNFINISHED_LATIN.search(unicodedata.normalize("NFKC", combined)) or any(
        phrase in combined for phrase in UNFINISHED_ZH
    ):
        _append(findings, "unfinished_marker",
                "content contains an unfinished or template marker")

    language_finding = simplified_finding(combined)
    if language_finding is not None:
        findings.append(language_finding)

    findings.extend(transcript_copy_findings(summary, takeaways, transcript_text))
    return findings


def transcript_copy_findings(
    summary: str,
    takeaways: List[str],
    transcript_text: str,
) -> List[Finding]:
    """Detect a summary or a takeaway that is the transcript pasted in."""
    findings: List[Finding] = []
    transcript_lines = [
        comparison_text(line)
        for line in normalize_text(transcript_text).splitlines()
        if han_count(line) >= MIN_COPY_LINE_HAN
    ]
    if not transcript_lines:
        return findings
    normalized_summary = comparison_text(summary)
    copied = sum(1 for line in transcript_lines if line and line in normalized_summary)
    if copied / len(transcript_lines) > MAX_COPIED_LINE_RATIO:
        _append(findings, "transcript_copy",
                "summary_zh copies excessive transcript text verbatim")
    normalized_transcript = comparison_text(transcript_text)
    if any(
        han_count(item) >= MIN_COPY_LINE_HAN and comparison_text(item) in normalized_transcript
        for item in takeaways
    ):
        _append(findings, "takeaway_transcript_copy",
                "a takeaway copies a long transcript passage verbatim")
    return findings


__all__ = [
    "GENERIC_TITLE",
    "MAX_SUMMARY_HAN",
    "MAX_TAKEAWAY_HAN",
    "MAX_TITLE_CHARS",
    "MIN_SUMMARY_HAN",
    "TAIWAN_CONTEXT_TERMS",
    "UNFINISHED_LATIN",
    "UNFINISHED_ZH",
    "comparison_text",
    "han_count",
    "normalize_text",
    "simplified_finding",
    "transcript_copy_findings",
    "validate_segment_content",
]
