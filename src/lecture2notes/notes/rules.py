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
from typing import Any, List, Mapping, Optional, Tuple

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


def forbidden_term_matches(
    text: str,
    context: str,
    terms: Mapping[str, Mapping[str, Mapping[str, str]]],
) -> List[Tuple[str, str, Mapping[str, str]]]:
    """``(layer, wrong, entry)`` for every forbidden spelling in *text*.

    *terms* is the profile's ``[check.forbidden_terms]`` as
    :func:`lecture2notes.profiles.loader.forbidden_terms` returns it. *text*
    is what is judged (quotations already removed); *context* is where a
    context-layer cue may appear (the same line, that segment's transcript).
    A context term inside an always-layer hit is not counted twice:
    遠端轉移 is one mistake, not two.
    """
    hits: List[Tuple[str, str, Mapping[str, str]]] = []
    always = terms.get("always") or {}
    masked = text
    for wrong, entry in always.items():
        if wrong in masked:
            hits.append(("always", wrong, entry))
    for wrong in always:
        masked = masked.replace(wrong, "　" * len(wrong))
    folded = context.casefold()
    for wrong, entry in (terms.get("context") or {}).items():
        cue = str(entry.get("cue") or "").casefold()
        if cue and wrong in masked and cue in folded:
            hits.append(("context", wrong, entry))
    return hits


def forbidden_term_findings(
    data: Mapping[str, Any],
    terms: Mapping[str, Mapping[str, Mapping[str, str]]],
    transcripts: Optional[List[str]] = None,
) -> List[Finding]:
    """`check json`'s half of R12: the JSON fields a note quotes verbatim.

    ``takeaways_zh`` becomes the note's Summary word for word and
    ``summary_zh`` / ``bullets_zh[].text`` become each section's draft, so a
    wrong translation caught here never reaches the note. Warnings, like R12.
    A context term needs its cue in the same string or in that segment's
    transcript (*transcripts*, one string per segment, may be omitted).
    """
    findings: List[Finding] = []
    if not any(terms.values()):
        return findings

    def scan(text: Any, where: str, said: str = "",
             index: Optional[int] = None) -> None:
        if not isinstance(text, str) or not text:
            return
        judged = re.sub(r"「[^」]*」", "", text)
        for layer, wrong, entry in forbidden_term_matches(
            judged, text + "\n" + said, terms
        ):
            english = entry.get("english") or ""
            findings.append(Finding(
                "warn", "forbidden_term",
                "「%s」 -> 「%s」%s [%s]" % (
                    wrong, entry.get("correct") or "",
                    " (%s)" % english if english else "", layer,
                ),
                index, None, where,
            ))

    for position, item in enumerate(data.get("takeaways_zh") or []):
        scan(item, "takeaways_zh[%d]" % position)
    segments = data.get("segments")
    for number, segment in enumerate(segments if isinstance(segments, list) else [], 1):
        if not isinstance(segment, Mapping):
            continue
        said = ""
        if transcripts and number - 1 < len(transcripts):
            said = transcripts[number - 1]
        scan(segment.get("summary_zh"), "segments[%d].summary_zh" % number,
             said, number)
        for position, bullet in enumerate(segment.get("bullets_zh") or []):
            text = bullet.get("text") if isinstance(bullet, Mapping) else bullet
            scan(text, "segments[%d].bullets_zh[%d].text" % (number, position),
                 said, number)
    return findings


#: An e-mail address, the one personal identifier a participant panel or a
#: chat window reliably puts on screen, and so into ``frame_ocr``.
EMAIL_PATTERN = re.compile(r"\S+@\S+\.\w+")


def frame_ocr_email_findings(data: Mapping[str, Any]) -> List[Finding]:
    """Warn on ``frame_ocr`` text that carries an e-mail address.

    A frame showing the participant panel or the chat hands its names and
    addresses to OCR, and from there into the document. Names cannot be
    matched mechanically; addresses can, and where there is one the rest of
    the panel is usually beside it.
    """
    findings: List[Finding] = []
    segments = data.get("segments")
    for number, segment in enumerate(segments if isinstance(segments, list) else [], 1):
        if not isinstance(segment, Mapping):
            continue
        for position, entry in enumerate(segment.get("frame_ocr") or []):
            text = entry.get("text") if isinstance(entry, Mapping) else None
            if isinstance(text, str) and EMAIL_PATTERN.search(text):
                findings.append(Finding(
                    "warn", "frame_ocr_email",
                    "frame_ocr text looks like it carries an e-mail address; "
                    "exclude participant-panel and chat frames (l2n ocr "
                    "--skip-frames) or clear this frame_ocr entry",
                    number, None,
                    "segments[%d].frame_ocr[%d]" % (number, position),
                ))
    return findings


#: A URL that carries a record or account identifier: a query parameter named
#: like one, or a long run of digits as a path segment. Seen in frames that
#: caught the browser's address bar over a records system.
URL_ID_PATTERN = re.compile(
    r"(?:https?://|www\.)\S*?(?:[?&](?:id|uid|pid|mrn|chart|chartno|patient|patientid|acc|accession|user|account)"
    r"=[\w-]{4,}|/\d{6,}(?:[/?#]|\b))",
    re.IGNORECASE,
)


def frame_ocr_url_id_findings(data: Mapping[str, Any]) -> List[Finding]:
    """Warn on ``frame_ocr`` text with an address-bar URL that carries an ID."""
    findings: List[Finding] = []
    segments = data.get("segments")
    for number, segment in enumerate(segments if isinstance(segments, list) else [], 1):
        if not isinstance(segment, Mapping):
            continue
        for position, entry in enumerate(segment.get("frame_ocr") or []):
            text = entry.get("text") if isinstance(entry, Mapping) else None
            if isinstance(text, str) and URL_ID_PATTERN.search(text):
                findings.append(Finding(
                    "warn", "frame_ocr_url_id",
                    "frame_ocr text shows a URL carrying an identifier (address bar of "
                    "a records system?); pick another frame, crop it, or clear this "
                    "frame_ocr entry",
                    number, None,
                    "segments[%d].frame_ocr[%d]" % (number, position),
                ))
    return findings


#: A number worth checking against what was said: two or more digits, or a
#: decimal. A lone digit is too often a list position or a count in prose.
SEGMENT_NUMBER = re.compile(r"(?<![\d.])(\d+(?:[.,]\d+)+|\d{2,})(?![\d])")
#: Removed from a claim before numbers are read from it: a timecode
#: (``00:11:21`` is not the numbers 11 and 21) and a timecode link's URL.
CLAIM_NOISE = re.compile(r"\[▶[^\]]*\]\([^)]*\)|\d{1,2}:\d{2}(?::\d{2})?")
#: A clause that says its value comes from a slide; its numbers are the
#: writer's own declaration and are not reported.
SLIDE_DECLARED = re.compile(r"投影片|簡報|[Ss]lide")
#: Where one clause ends and the next begins.
CLAUSE_BREAK = re.compile(r"[。；;！？!?\n]")
#: A four-digit year (a citation's year, not a measurement).
YEAR = re.compile(r"^(?:19|20)\d{2}$")

_ZH_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_ZH_UNITS = {"十": 10, "百": 100, "千": 1000, "萬": 10000}
ZH_NUMBER = re.compile(r"[零〇一二兩三四五六七八九十百千萬]+(?:點[零〇一二三四五六七八九]+)?")
_EN_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_EN_TENS = {w: 10 * i for i, w in enumerate(
    "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
EN_NUMBER = re.compile(
    r"\b(?:(%s)(?:[\s-]+(%s))?|(%s))(?:\s+hundred)?\b" % (
        "|".join(_EN_TENS), "|".join(w for w in _EN_UNITS if _EN_UNITS[w] < 10),
        "|".join(_EN_UNITS)),
    re.IGNORECASE,
)


def _zh_integer(text: str) -> Optional[int]:
    total, section, number = 0, 0, 0
    for char in text:
        if char in _ZH_DIGITS:
            number = _ZH_DIGITS[char]
        elif char in _ZH_UNITS:
            unit = _ZH_UNITS[char]
            if unit == 10000:
                total += (section + number) * unit
                section, number = 0, 0
            else:
                section += (number or 1) * unit
                number = 0
        else:
            return None
    return total + section + number


def _zh_to_digits(match: "re.Match[str]") -> str:
    whole, _, frac = match.group(0).partition("點")
    value = _zh_integer(whole) if whole else 0
    if value is None:
        return match.group(0)
    if frac:
        return "%d.%s" % (value, "".join(str(_ZH_DIGITS[c]) for c in frac))
    return str(value)


def _en_to_digits(match: "re.Match[str]") -> str:
    tens, unit, single = match.group(1), match.group(2), match.group(3)
    value = (_EN_TENS[tens.lower()] + (_EN_UNITS[unit.lower()] if unit else 0)) if tens else _EN_UNITS[single.lower()]
    if match.group(0).lower().endswith("hundred"):
        value *= 100
    return str(value)


def spoken_numbers_text(text: str) -> str:
    """Spoken numbers written as digits: 十五 -> 15, 三點四 -> 3.4,
    twenty five -> 25, two hundred -> 200. Digits already there are kept."""
    text = ZH_NUMBER.sub(_zh_to_digits, text or "")
    return EN_NUMBER.sub(_en_to_digits, text)


def _numbers(text: str) -> List[str]:
    """Numbers as written, commas dropped. A zero-padded ``01`` is a label
    (段落 01, 條列 01-A), not a value anyone said."""
    out = []
    for match in SEGMENT_NUMBER.finditer(text or ""):
        value = match.group(1).replace(",", "")
        if not (value.startswith("0") and len(value) > 1 and not value.startswith("0.")):
            out.append(value)
    return out


def claimed_numbers(text: str) -> List[str]:
    """The numbers a summary or bullet states as said: timecodes, years and
    clauses that name a slide as their source are left out."""
    out: List[str] = []
    for clause in CLAUSE_BREAK.split(CLAIM_NOISE.sub(" ", text or "")):
        if SLIDE_DECLARED.search(clause):
            continue
        out.extend(v for v in _numbers(clause) if not YEAR.match(v))
    return out


def segment_number_findings(
    data: Mapping[str, Any], transcripts: Sequence[str]
) -> List[Finding]:
    """Warn on a number in a segment's summary or bullets the segment never says.

    ``transcripts`` is each segment's spoken text (parallel to ``segments``).
    A number found only in the segment's slide OCR is reported as slide-only
    -- a value on the slide is not a value the speaker stated -- and one found
    only in another segment's speech names that segment, which is what a
    summary written against the wrong stretch of the lecture looks like.
    """
    segments = [s for s in (data.get("segments") or []) if isinstance(s, Mapping)]
    spoken = [set(_numbers(spoken_numbers_text(t))) for t in transcripts]
    spoken += [set()] * (len(segments) - len(transcripts))
    findings: List[Finding] = []
    for number, segment in enumerate(segments, 1):
        if not transcripts or number > len(transcripts) or not transcripts[number - 1].strip():
            continue
        bullets = [b.get("text") if isinstance(b, Mapping) else b for b in segment.get("bullets_zh") or []]
        claimed = claimed_numbers("\n".join([str(segment.get("summary_zh") or "")] + [str(b or "") for b in bullets]))
        slides = set()
        for entry in segment.get("frame_ocr") or []:
            if isinstance(entry, Mapping):
                slides.update(_numbers(str(entry.get("text") or "")))
        for value in sorted(set(claimed), key=claimed.index):
            if value in spoken[number - 1]:
                continue
            elsewhere = [i + 1 for i, said in enumerate(spoken) if value in said and i + 1 != number]
            if value in slides:
                why = "is on the segment's slide but the speaker does not say it"
            elif elsewhere:
                why = "is said in segment %s, not in this one" % ", ".join(str(i) for i in elsewhere[:3])
            else:
                why = "is not in this segment's transcript or slides"
            findings.append(Finding(
                "warn", "number_unsupported",
                "segment %d: %s %s" % (number, value, why),
                number, None, "segments[%d]" % number,
            ))
    return findings


__all__ = [
    "EMAIL_PATTERN",
    "segment_number_findings",
    "URL_ID_PATTERN",
    "frame_ocr_url_id_findings",
    "forbidden_term_findings",
    "forbidden_term_matches",
    "frame_ocr_email_findings",
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
