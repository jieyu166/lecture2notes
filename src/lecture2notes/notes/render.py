"""Deterministic skeleton note, projected only from the canonical JSON.

Ported from rad-workflow
.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/render_v4_note.py

Nothing here calls a model. Every line in the output comes from a key in the
document, so rendering the same JSON twice produces the same bytes; that is what
lets an audit compare a published note against its source and get a real answer.
The LLM expansion step reads this skeleton and writes back over it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from lecture2notes.notes import guideline
from lecture2notes.profiles import loader
from lecture2notes.schema.model import AI_DRAFT_MARK as _AI_DRAFT_MARK
from lecture2notes.schema.model import segment_end, segment_start


def text_of(value: Any) -> str:
    return value if isinstance(value, str) else ""


def strings_of(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def bullet_texts(segment: Mapping[str, Any]) -> List[str]:
    """Read bullets from either the legacy string list or the v2 object list."""
    raw = segment.get("takeaways_zh")
    if raw is None:
        raw = segment.get("bullets_zh")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
            out.append(item["text"])
    return out


def render_note(data: Mapping[str, Any]) -> str:
    """Render the skeleton note as Markdown."""
    title = text_of(data.get("title")) or "Lecture"
    lines: List[str] = ["# %s" % title, ""]
    segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    for position, segment in enumerate(segments, 1):
        if not isinstance(segment, Mapping):
            continue
        start = segment_start(segment)
        end = segment_end(segment)
        lines.extend([
            "## %d. %s" % (position, text_of(segment.get("title"))),
            "",
            "> %.3fs-%.3fs" % (start, end),
            "",
            text_of(segment.get("summary_zh")),
            "",
            "### Takeaways",
        ])
        lines.extend("- %s" % item for item in bullet_texts(segment))

        editorial = strings_of(segment.get("editorial_notes_zh"))
        if editorial:
            lines.extend(["", "> [!note] Editorial"])
            lines.extend("> - %s" % item for item in editorial)

        frames = segment.get("frames") if isinstance(segment.get("frames"), list) else []
        lines.extend(["", "### Frames"])
        for frame in frames:
            if not isinstance(frame, Mapping):
                continue
            path = text_of(frame.get("path")).replace("\\", "/")
            if not path:
                continue
            try:
                timestamp = float(frame.get("time"))
            except (TypeError, ValueError):
                continue
            lines.append("![[%s]] <!-- %.3fs -->" % (path, timestamp))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_note(path: Path, data: Mapping[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_note(data), encoding="utf-8", newline="\n")
    return destination


# ==========================================================================
# the skeleton note: layer one of the two-layer note generation
# ==========================================================================
# `render_note` above is the legacy projection the audit compares a note
# against; it stays as it was ported. Everything below is the skeleton the
# product actually ships: fixed sections in a fixed order, every line projected
# from a key in the document, no model involved. An agent then expands this file
# in place under the writing guideline, and `l2n check note` decides whether the
# expansion stayed inside the contract.

#: Styles `--style` accepts. faithful keeps every quote; concise keeps one.
STYLES = ("faithful", "concise")

#: Placeholder syntax shared by the frontmatter and the note template.
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

#: A stem that starts with a date, as the hospital and course exports write it.
STEM_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})")

#: The correction table's header. `check note` looks for these column names, so
#: they are defined once and rendered from here.
CORRECTION_COLUMNS = ("heard", "correct", "source")

#: How an unverified term is marked, in the note and in the check's message.
UNVERIFIED_MARK = "未寫入本文"

#: Marks a placeholder a model still has to write: the speaker-outline rows and
#: the drafted questions. Whoever writes the placeholder deletes the marker with
#: it, so `ai_draft_remaining` counts what is left to do and zero is reachable.
#: It is deliberately *not* on the reader's two Summary slots or on the
#: remember-three candidates -- those are the reader's to keep, delete or
#: rewrite, and no model finishes them. Defined in `schema.model` because the
#: scaffold stage writes the same marker into the JSON; re-exported here under
#: the name every caller in this package already uses.
AI_DRAFT_MARK = _AI_DRAFT_MARK

#: How many candidates the model drafts under "我應該記住的 3 件事".
REMEMBER_COUNT = 3

#: Which contract this note was written to, recorded in the note itself.
#: `check note` defaults to the profile's style, and a note rendered faithful
#: then checked concise skips R6 entirely -- the one rule faithful adds. The
#: field run hit exactly that and passed a note with no quotation anywhere.
#: Both values are fixed by the inputs, so the line does not affect determinism.
STYLE_MARKER = "<!-- l2n:style=%s guideline=%s -->"

#: Reads the marker back. Tolerant of spacing, because a person may retype it.
STYLE_MARKER_RE = re.compile(
    r"<!--\s*l2n:style=([A-Za-z_]+)\s+guideline=([0-9.]+)\s*-->"
)


def style_marker(style: str) -> str:
    """The one line that says which rules this note was written under."""
    return STYLE_MARKER % (style, guideline.GUIDELINE_VERSION)


def read_style_marker(text: str) -> Optional[str]:
    """The style a note declares, or None when it declares nothing."""
    match = STYLE_MARKER_RE.search(text)
    return match.group(1) if match else None


def insert_style_marker(text: str, style: str) -> str:
    """Put the marker straight after the frontmatter, or at the very top.

    After the fence rather than inside it: the frontmatter is the profile's to
    shape, and an extra YAML key would appear in every Obsidian property list.
    """
    if read_style_marker(text) is not None:
        return text
    marker = style_marker(style)
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for position in range(1, len(lines)):
            if lines[position].strip() == "---":
                return "\n".join(
                    lines[: position + 1] + [marker] + lines[position + 1:]
                )
    return "\n".join([marker] + lines)


#: The skeleton's first body line. A list an agent filled in reads exactly like
#: one a person wrote, and a reader three months later cannot tell them apart,
#: so the file says which it is before anything else on the page.
DRAFT_DECLARATION = "> 本筆記為模型產出的初稿，未經本人確認。"

#: The reader's two slots in Summary. They carry a sentence opening rather than
#: a question: an empty box gets skipped, a half-written sentence gets finished.
SUMMARY_SLOTS = ("開這篇之前我卡在___", "這篇沒回答到的是___")

#: What the slots say about the option nobody remembers they have.
SUMMARY_SLOT_NOTE = "（兩格都填不出來就選「丟棄」。模型不要代填，也不要刪。）"

#: The model's three candidates live behind this, collapsed. A list that arrives
#: already full invites agreement; one that arrives folded has to be opened.
CANDIDATE_CALLOUT = "> [!note]- 模型候選（未經本人確認）"

#: The learning-verification checklist. Fixed text, so two renders agree.
VERIFICATION_ITEMS = (
    "能用自己的話說出本講座的核心觀念，不看筆記也說得完整",
    "能指出本講座中最容易混淆的一組觀念，並說明它們的差異",
    "能說出本講座的結論在什麼情況下不成立，或講者明說沒有把握的地方",
)


def fill(template: str, context: Mapping[str, Any]) -> str:
    """Substitute ``{{name}}`` from *context*; an unknown name renders empty.

    Unknown placeholders are empty rather than fatal on purpose: a user overlay
    template is allowed to ask for a field this package knows nothing about, and
    losing the line is better than losing the note.
    """
    return PLACEHOLDER.sub(
        lambda match: str(context.get(match.group(1), "") or ""), template
    )


def frontmatter_block(template: str) -> str:
    """The ``---`` fenced block of a frontmatter template, comments discarded.

    A template file may carry a comment header explaining its placeholders; only
    what lies between the fences becomes the note's frontmatter.
    """
    lines = template.replace("\r\n", "\n").split("\n")
    try:
        opening = next(i for i, line in enumerate(lines) if line.strip() == "---")
    except StopIteration:
        return ""
    for position in range(opening + 1, len(lines)):
        if lines[position].strip() == "---":
            return "\n".join(lines[opening:position + 1]) + "\n"
    return ""


def frontmatter_keys(block: str) -> List[str]:
    """Top-level YAML keys of a rendered frontmatter block, in order."""
    keys: List[str] = []
    for line in block.replace("\r\n", "\n").split("\n"):
        if line.strip() in ("---", ""):
            continue
        if line[:1].isspace() or line.lstrip().startswith("#"):
            continue
        name, separator, _ = line.partition(":")
        if separator and name.strip():
            keys.append(name.strip())
    return keys


def clock(seconds: Any) -> str:
    """``HH:MM:SS``, or an empty string when there is no usable number."""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    return "%02d:%02d:%02d" % (total // 3600, (total % 3600) // 60, total % 60)


def _stem_of(data: Mapping[str, Any], stem: Optional[str] = None) -> str:
    if stem:
        return stem
    return text_of(data.get("stem"))


def _date_of(data: Mapping[str, Any], stem: str) -> str:
    """The lecture's date: the document's own field, else the stem's prefix.

    Never today's date. A skeleton that changes because it was rendered on a
    different day is not deterministic, and determinism is the whole point.
    """
    explicit = text_of(data.get("date"))
    if explicit:
        return explicit
    match = STEM_DATE.match(stem)
    if match:
        return "%s-%s-%s" % match.groups()
    return ""


def _subtitle(data: Mapping[str, Any]) -> Mapping[str, Any]:
    source = data.get("source")
    if not isinstance(source, Mapping):
        return {}
    subtitle = source.get("subtitle")
    return subtitle if isinstance(subtitle, Mapping) else {}


def frontmatter_context(
    data: Mapping[str, Any], stem: Optional[str] = None
) -> Dict[str, str]:
    """Every value a frontmatter template may ask for, already as text."""
    resolved_stem = _stem_of(data, stem)
    source = data.get("source") if isinstance(data.get("source"), Mapping) else {}
    subtitle = _subtitle(data)
    tags = strings_of(data.get("tags"))
    segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    duration = data.get("duration_sec")
    try:
        duration_min = "%d" % int(round(float(duration) / 60.0))
    except (TypeError, ValueError):
        duration_min = ""
    return {
        "title": text_of(data.get("title")),
        "date": _date_of(data, resolved_stem),
        "stem": resolved_stem,
        "profile": text_of(data.get("profile")),
        "source_video": text_of(source.get("video")),
        "source_subtitle": text_of(subtitle.get("path")),
        "source_origin": text_of(subtitle.get("origin")),
        "source_engine": text_of(subtitle.get("engine")),
        "source_lang": text_of(subtitle.get("lang")),
        "duration_min": duration_min,
        "segment_count": str(len(segments)),
        "tags": ", ".join('"%s"' % tag for tag in tags),
    }


def render_frontmatter(
    data: Mapping[str, Any],
    template: Optional[str] = None,
    stem: Optional[str] = None,
    profile: str = loader.BUILTIN_PROFILE,
) -> str:
    """Render the frontmatter from a template file, never from code."""
    text = template if template is not None else loader.frontmatter_template(profile)
    if not text:
        return ""
    return fill(frontmatter_block(text), frontmatter_context(data, stem))


# -- the body ---------------------------------------------------------------
def quote_items(segment: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """``quotes_zh`` as objects, accepting the 1.x bare-string spelling."""
    raw = segment.get("quotes_zh")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str) and item:
            out.append({"text": item, "t": None})
        elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
            if item["text"]:
                out.append({"text": item["text"], "t": item.get("t")})
    return out


def bullet_items(segment: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """``bullets_zh`` as objects, carrying the kind so concise can drop quotes."""
    raw = segment.get("bullets_zh")
    if raw is None:
        raw = segment.get("takeaways_zh")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str) and item:
            out.append({"text": item, "t": None, "kind": "synthesis"})
        elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
            if item["text"]:
                out.append({
                    "text": item["text"],
                    "t": item.get("t"),
                    "kind": str(item.get("kind") or "synthesis"),
                })
    return out


# -- timecode links (schema 2.1) --------------------------------------------
# With a media base URL, every time the skeleton prints becomes a link that
# opens the video at that second (an MP4 media fragment, ``#t=<seconds>``):
# ``[▶ 12:05](<base>/<video>#t=725)``. The base URL is never in this package:
# it comes from ``--media-base-url``, ``L2N_MEDIA_BASE_URL`` or the overlay's
# ``[note] media_base_url``. Without one, times print as plain text, exactly as
# before. `check note` reads a link back as its label, so every rule sees the
# same text whichever way the note was rendered.

#: The visible part of a timecode link, and what `check note` reduces it to.
TIME_LINK = re.compile(r"\[▶\s*([^\]]*)\]\([^)\s]*\)")


def short_clock(seconds: Any) -> str:
    """``mm:ss``, or ``h:mm:ss`` past an hour; empty when there is no number."""
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    hours, rest = divmod(total, 3600)
    if hours:
        return "%d:%02d:%02d" % (hours, rest // 60, rest % 60)
    return "%02d:%02d" % (rest // 60, rest % 60)


class MediaLink(str):
    """A video URL that also knows where the browser's clock starts for it.

    A file whose container starts at 3.8 s plays from 3.8 s in a browser, so a
    ``#t=`` fragment needs that much added to the document's time. Being a
    ``str`` keeps every caller that only wants the URL unchanged.
    """

    offset: float = 0.0

    def __new__(cls, url: str, offset: float = 0.0) -> "MediaLink":
        link = super().__new__(cls, url)
        link.offset = float(offset or 0.0)
        return link


def video_url(
    data: Mapping[str, Any], base: Optional[str], offset: float = 0.0
) -> Optional[str]:
    """``<base>/<source.video>``, URL-quoted; None without a base or a video."""
    from urllib.parse import quote

    if not base:
        return None
    source = data.get("source") if isinstance(data.get("source"), Mapping) else {}
    name = text_of(source.get("video")) or ("%s.mp4" % text_of(data.get("stem")))
    return MediaLink("%s/%s" % (base.rstrip("/"), quote(Path(name).name)), offset)


def time_link(seconds: Any, url: Optional[str], label: str = "") -> str:
    """``[▶ label mm:ss](url#t=s)``, or the bare ``mm:ss`` (or clock) without a URL."""
    if url is None:
        stamp = clock(seconds)
        return ("%s %s" % (label, stamp)).strip() if stamp else ""
    stamp = short_clock(seconds)
    if not stamp:
        return ""
    text = ("%s %s" % (label, stamp)).strip()
    return "[▶ %s](%s#t=%d)" % (
        text, url, int(float(seconds) + float(getattr(url, "offset", 0.0)))
    )


def _timestamp_suffix(value: Any, url: Optional[str] = None) -> str:
    stamp = time_link(value, url)
    return " (%s)" % stamp if stamp else ""


def novelty_of(segment: Mapping[str, Any]) -> str:
    triage = segment.get("triage")
    return text_of(triage.get("novelty")) if isinstance(triage, Mapping) else ""


#: The one line a segment the reader already knows gets instead of a body.
KNOWN_LINE = "已知，不重寫：見 %s。%s"


def render_known_segment(segment: Mapping[str, Any], position: int, url: Optional[str]) -> List[str]:
    """A 已知 segment: heading, time, one line pointing at the note that covers it."""
    triage = segment.get("triage") if isinstance(segment.get("triage"), Mapping) else {}
    notes = "、".join(strings_of(triage.get("related_notes"))) or "既有筆記"
    reason = text_of(triage.get("reason"))
    lines = ["## %d、%s" % (position, text_of(segment.get("title")))]
    span = _span(segment, url)
    if span:
        lines.extend(["", span])
    lines.extend(["", KNOWN_LINE % (notes, ("（%s）" % reason) if reason else "")])
    return lines


def _span(segment: Mapping[str, Any], url: Optional[str]) -> str:
    start = time_link(segment.get("start_sec"), url)
    end = short_clock(segment.get("end_sec")) if url else clock(segment.get("end_sec"))
    if not start and not end:
        return ""
    return "(%s - %s)" % (start, end)


def render_segment(
    segment: Mapping[str, Any], position: int, style: str = "concise",
    url: Optional[str] = None,
) -> List[str]:
    """One ``## n、title`` section: embed, summary, quotes, bullets, in order.

    A segment triaged 已知 gets one line and a link to the note that already
    covers it instead: rewriting what the reader knows is the cost triage is
    there to remove.
    """
    if novelty_of(segment) == "已知":
        return render_known_segment(segment, position, url)
    lines = ["## %d、%s" % (position, text_of(segment.get("title")))]
    span = _span(segment, url)
    if span:
        lines.extend(["", span])

    frame = segment.get("frame")
    if isinstance(frame, str) and frame:
        lines.extend(["", "![[%s]]" % frame.replace("\\", "/")])

    summary = text_of(segment.get("summary_zh"))
    if summary:
        lines.extend(["", summary])

    quotes = quote_items(segment)
    if style == "concise":
        quotes = quotes[:1]
    for quote in quotes:
        lines.extend([
            "", "> 「%s」%s" % (quote["text"], _timestamp_suffix(quote.get("t"), url))
        ])

    bullets = bullet_items(segment)
    if style == "concise":
        bullets = [item for item in bullets if item["kind"] != "quote"]
    if bullets:
        lines.append("")
        for bullet in bullets:
            if bullet["kind"] == "quote":
                lines.append(
                    "- 「%s」%s" % (bullet["text"], _timestamp_suffix(bullet.get("t"), url))
                )
            elif url and bullet.get("t") is not None:
                lines.append("- %s%s" % (bullet["text"], _timestamp_suffix(bullet.get("t"), url)))
            else:
                lines.append("- %s" % bullet["text"])
    return lines


def render_references(data: Mapping[str, Any]) -> List[str]:
    """The correction table, the unverified terms, and the source block."""
    lines = ["| %s |" % " | ".join(CORRECTION_COLUMNS),
             "| %s |" % " | ".join("---" for _ in CORRECTION_COLUMNS)]
    corrections = data.get("corrections")
    rows = corrections if isinstance(corrections, list) else []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        lines.append("| %s |" % " | ".join(
            text_of(row.get(column)) or "-" for column in CORRECTION_COLUMNS
        ))

    lines.extend(["", "#### unverified_terms", ""])
    terms = strings_of(data.get("unverified_terms"))
    if terms:
        lines.extend("- %s（%s）" % (term, UNVERIFIED_MARK) for term in terms)
    else:
        lines.append("- （無）")

    lines.extend(["", "#### source", ""])
    source = data.get("source") if isinstance(data.get("source"), Mapping) else {}
    subtitle = _subtitle(data)
    lines.append("- video: %s" % (text_of(source.get("video")) or "-"))
    lines.append("- subtitle: %s" % (text_of(subtitle.get("path")) or "-"))
    lines.append("- subtitle origin: %s" % (text_of(subtitle.get("origin")) or "-"))
    lines.append("- engine: %s" % (text_of(subtitle.get("engine")) or "-"))
    if subtitle.get("offset_model") is not None:
        lines.append("- offset model: %s" % subtitle.get("offset_model"))
    return lines


#: What an unanswered draft question carries until the expansion fills it in.
ANSWER_PLACEHOLDER = "（擴寫時填入答案，並標出對應時間碼）"

#: Indent that keeps a callout inside its numbered list item.
ANSWER_INDENT = "   "

#: Used when the document has too few segments to pair two of them.
QUESTION_FILLER = "（素材不足，擴寫時自行補一題問理由或邊界的題目）"

#: The line that says what this section is for, so nobody treats it as decoration.
QUESTION_NOTE = "回看是答題，不是重讀：先自己答完，再展開下面的答案。"


def segment_titles(data: Mapping[str, Any]) -> List[str]:
    """Every segment title that is a non-empty string, in document order."""
    segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    titles: List[str] = []
    for segment in segments:
        if isinstance(segment, Mapping) and text_of(segment.get("title")):
            titles.append(text_of(segment.get("title")))
    return titles


def _inference_draft(titles: Sequence[str]) -> str:
    """The one question that cannot be answered by scanning the note.

    Its shape is "if a case has A but not B, does the conclusion still hold" --
    a question about the boundary of the claim, not about what a term means. A
    definition question is answerable by rereading, which is exactly the habit
    this section exists to break.
    """
    if len(titles) >= 2:
        return "%s 若某個案例具備「%s」但缺少「%s」，這篇的結論還成立嗎？" % (
            guideline.INFERENCE_MARK, titles[0], titles[-1]
        )
    if titles:
        return "%s 若把「%s」的前提拿掉一個，這篇的結論還成立嗎？" % (
            guideline.INFERENCE_MARK, titles[0]
        )
    return "%s 若把本講座的前提拿掉一個，結論還成立嗎？" % guideline.INFERENCE_MARK


def explicit_questions(data: Mapping[str, Any]) -> List[str]:
    """The questions the segmentation step already wrote, if it wrote any.

    ``questions_zh`` is optional, so this is allowed to come back empty and the
    skeleton falls back to drafting its own from the segment titles.
    """
    raw = data.get("questions_zh")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        if isinstance(item, Mapping) and text_of(item.get("text")).strip():
            out.append(text_of(item["text"]).strip())
    return out


def question_drafts(data: Mapping[str, Any]) -> List[str]:
    """Three to five draft questions, the last one inferential.

    The section is never allowed to be empty, because answering is what makes a
    lecture stick and rereading is not: retrieval practice is the high-utility
    study technique and rereading is the low-utility one (Karpicke & Blunt 2011;
    Dunlosky 2013). Questions are drafted across segments rather than one per
    segment, so answering one cannot be done by looking at a single section.

    ``questions_zh`` wins when the segmentation step already wrote a usable set;
    a set that is short or has no inference question is kept and topped up
    rather than discarded.
    """
    titles = segment_titles(data)
    written = explicit_questions(data)[:guideline.MAX_QUESTIONS]
    if len(written) >= guideline.MIN_QUESTIONS and any(
        guideline.INFERENCE_MARK in question for question in written
    ):
        return written
    drafts: List[str] = written[:guideline.MAX_QUESTIONS - 1]
    for position, title in enumerate(titles[:guideline.MAX_QUESTIONS - 1]):
        if len(drafts) >= guideline.MAX_QUESTIONS - 1:
            break
        other = titles[(position + 1) % len(titles)]
        if other == title:
            drafts.append("不看筆記說出「%s」這一段的推理鏈，講者的理由是什麼？" % title)
        elif position % 2 == 0:
            drafts.append(
                "不看筆記說出「%s」與「%s」的關聯，講者為什麼把兩者放在一起？"
                % (title, other)
            )
        else:
            drafts.append(
                "不看筆記說出「%s」的結論，在「%s」的情境下會怎麼變？講者的理由是什麼？"
                % (title, other)
            )
    while len(drafts) < guideline.MIN_QUESTIONS - 1:
        drafts.append(QUESTION_FILLER)
    drafts.append(_inference_draft(titles))
    return drafts


#: With per-segment questions (schema 2.1), the section carries at least this
#: many: one per segment, then the cross-segment ``questions_zh``, then drafts.
SEGMENT_QUESTION_MIN = 8


def segment_questions(data: Mapping[str, Any]) -> List[Tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """``(segment, question)`` for every segment that carries a 2.1 question."""
    segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    out = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        question = segment.get("question")
        if isinstance(question, Mapping) and text_of(question.get("q")).strip():
            out.append((segment, question))
    return out


def render_questions(data: Mapping[str, Any], url: Optional[str] = None) -> List[str]:
    """The 題目 section, each question with a collapsed answer.

    A 2.1 document already has one question per segment with its answer and
    the second the speaker explains it; those come first, answered, with a
    link back to that second. Below :data:`SEGMENT_QUESTION_MIN`, the
    cross-segment ``questions_zh`` and then drafts fill the rest, and one
    inference question is always there. A document without segment questions
    keeps the drafted section it always had.
    """
    answered = segment_questions(data)
    if not answered:
        lines: List[str] = [QUESTION_NOTE, "", AI_DRAFT_MARK, ""]
        for number, question in enumerate(question_drafts(data), 1):
            if number > 1:
                lines.append("")
            lines.append("%d. %s" % (number, question))
            lines.append("%s%s 答案" % (ANSWER_INDENT, guideline.ANSWER_CALLOUT))
            lines.append("%s> %s" % (ANSWER_INDENT, ANSWER_PLACEHOLDER))
        return lines

    items: List[Tuple[str, str]] = []
    for segment, question in answered:
        where = time_link(question.get("answer_sec"), url, "講解處")
        answer = text_of(question.get("a")).strip() or ANSWER_PLACEHOLDER
        items.append((
            text_of(question.get("q")).strip(),
            "%s%s" % (answer, ("（%s）" % where) if where else ""),
        ))
    drafted = False
    for text in explicit_questions(data):
        if len(items) >= SEGMENT_QUESTION_MIN:
            break
        items.append((text, ANSWER_PLACEHOLDER))
        drafted = True
    if len(items) < SEGMENT_QUESTION_MIN:
        for text in question_drafts(data):
            if len(items) >= SEGMENT_QUESTION_MIN:
                break
            if text not in (q for q, _ in items):
                items.append((text, ANSWER_PLACEHOLDER))
                drafted = True
    if not any(q.startswith(guideline.INFERENCE_MARK) for q, _ in items):
        items.append((_inference_draft(segment_titles(data)), ANSWER_PLACEHOLDER))
        drafted = True

    lines = [QUESTION_NOTE, ""]
    if drafted:
        lines.extend([AI_DRAFT_MARK, ""])
    for number, (question, answer) in enumerate(items, 1):
        if number > 1:
            lines.append("")
        lines.append("%d. %s" % (number, question))
        lines.append("%s%s 答案" % (ANSWER_INDENT, guideline.ANSWER_CALLOUT))
        lines.append("%s> %s" % (ANSWER_INDENT, answer))
    return lines


# -- triage (schema 2.1) -------------------------------------------------------
TRIAGE_HEADING = "## 分流"


def _segment_minutes(segment: Mapping[str, Any]) -> float:
    try:
        return (float(segment.get("end_sec")) - float(segment.get("start_sec"))) / 60.0
    except (TypeError, ValueError):
        return 0.0


def _minutes(value: float) -> str:
    return ("%.1f" % value).rstrip("0").rstrip(".") if value < 10 else "%d" % round(value)


def render_triage(data: Mapping[str, Any], url: Optional[str] = None) -> List[str]:
    """``## 分流``: the verdict, the must-watch segments and one row per segment.

    Empty when the document carries no triage, so a template that asks for it
    loses nothing on an older document.
    """
    overall = data.get("triage_overall")
    segments = [s for s in (data.get("segments") or []) if isinstance(s, Mapping)]
    rows = [s for s in segments if isinstance(s.get("triage"), Mapping)]
    if not isinstance(overall, Mapping) and not rows:
        return []
    lines = [TRIAGE_HEADING, ""]
    if isinstance(overall, Mapping):
        total = sum(_segment_minutes(s) for s in segments)
        try:
            est = float(overall.get("est_minutes"))
        except (TypeError, ValueError):
            est = 0.0
        saved = total - est
        lines.append("**%s**（必看約 %s 分鐘／全片 %s 分鐘%s）" % (
            text_of(overall.get("verdict")) or "-", _minutes(est), _minutes(total),
            "，省下約 %s 分鐘" % _minutes(saved) if saved > 0.5 else "",
        ))
        must = [i for i in (overall.get("must_watch") or []) if isinstance(i, int)]
        picks = []
        for index in must:
            if 1 <= index <= len(segments):
                segment = segments[index - 1]
                link = time_link(segment.get("start_sec"), url)
                picks.append("第 %d 段「%s」%s" % (
                    index, text_of(segment.get("title")), (" " + link) if link else ""
                ))
        if picks:
            lines.extend(["", "必看：" + "；".join(picks)])
        reason = text_of(overall.get("reason"))
        if reason:
            lines.extend(["", "> %s" % reason])
    if rows:
        lines.extend(["", "| 段 | 時間 | 判斷 | 理由 | 既有筆記 |", "| --- | --- | --- | --- | --- |"])
        for position, segment in enumerate(segments, 1):
            triage = segment.get("triage")
            if not isinstance(triage, Mapping):
                continue
            lines.append("| %d %s | %s | %s | %s | %s |" % (
                position, text_of(segment.get("title")).replace("|", "／"),
                time_link(segment.get("start_sec"), url) or "-",
                text_of(triage.get("novelty")) or "-",
                text_of(triage.get("reason")).replace("|", "／") or "-",
                "、".join(strings_of(triage.get("related_notes"))) or "-",
            ))
    return lines


# -- Summary anchors -------------------------------------------------------------
#: How alike a takeaway and a segment must be (character-bigram overlap) before
#: the takeaway gets that segment's link, and by how much the best segment must
#: beat the runner-up. A takeaway that spans the lecture gets no link rather
#: than a wrong one.
ANCHOR_MIN_SCORE = 0.12
ANCHOR_MARGIN = 1.25


def _bigrams(text: str) -> set:
    compact = re.sub(r"\s+", "", text.lower())
    return {compact[i:i + 2] for i in range(len(compact) - 1)}


def takeaway_anchor(takeaway: str, segments: Sequence[Any]) -> Optional[float]:
    """The second a takeaway was said, when one segment clearly owns it.

    Within that segment, a timed bullet that overlaps the takeaway better than
    the segment as a whole gives its own ``t``; otherwise the segment's start.
    """
    wanted = _bigrams(takeaway)
    if not wanted:
        return None
    scored = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        text = " ".join([text_of(segment.get("title")), text_of(segment.get("summary_zh"))]
                        + [b["text"] for b in bullet_items(segment)])
        have = _bigrams(text)
        score = len(wanted & have) / float(len(wanted | have) or 1)
        scored.append((score, segment))
    scored.sort(key=lambda item: -item[0])
    if not scored or scored[0][0] < ANCHOR_MIN_SCORE:
        return None
    if len(scored) > 1 and scored[0][0] < scored[1][0] * ANCHOR_MARGIN:
        return None
    best = scored[0][1]
    timed = [b for b in bullet_items(best) if isinstance(b.get("t"), (int, float))]
    if timed:
        top = max(timed, key=lambda b: len(wanted & _bigrams(b["text"])))
        if len(wanted & _bigrams(top["text"])) >= len(wanted) * 0.5:
            return float(top["t"])
    try:
        return float(best.get("start_sec"))
    except (TypeError, ValueError):
        return None


#: Columns of one speaker-outline row, separated so a reader scans down one.
OUTLINE_SEPARATOR = " ｜ "

#: What the section is for. "Did", not "said": "opens with a misdiagnosis case,
#: 6% of the running time" transfers to the next lecture; "covered disc grading"
#: is only the table of contents again.
OUTLINE_NOTE = "每段一行，動詞開頭，只寫講者做了什麼，不寫他講了什麼。"

#: Said once above the rows, because it is a reading instruction, not data. The
#: ramp is the opening 5 to 8%, where the speaker says why this is worth doing
#: and where everyone before got stuck; it is the raw material for Evergreen.
OUTLINE_RAMP_NOTE = (
    "開頭 5 至 8% 是坡道（為什麼值得講、前人卡在哪），Evergreen 的原料在那裡；"
    "首段與末段呼應不起來，多半是坡道被併進了第 2 段，回頭檢查分段。"
)

#: Each row's third column until the expansion rewrites it.
OUTLINE_DRAFT = "（改寫成動詞開頭一句：講者在「%s」這一段做了什麼）"


def _span_seconds(segment: Mapping[str, Any]) -> float:
    """One segment's running time, or 0.0 when the times are unusable."""
    try:
        return max(segment_end(segment) - segment_start(segment), 0.0)
    except (KeyError, TypeError, ValueError):
        return 0.0


def segment_shares(segments: Sequence[Any]) -> List[int]:
    """Each segment's share of the running time, as whole percents summing to 100.

    Largest remainder rather than independent rounding: a column that adds up to
    99 sends the reader to check the arithmetic instead of reading the outline.
    """
    spans = [_span_seconds(segment) for segment in segments]
    if not spans:
        return []
    total = sum(spans)
    if total <= 0:
        base = 100 // len(spans)
        shares = [base] * len(spans)
        shares[0] += 100 - base * len(spans)
        return shares
    exact = [span * 100.0 / total for span in spans]
    shares = [int(value) for value in exact]
    order = sorted(
        range(len(exact)), key=lambda index: (int(exact[index]) - exact[index], index)
    )
    for position in order[:100 - sum(shares)]:
        shares[position] += 1
    return shares


def render_speaker_outline(data: Mapping[str, Any]) -> List[str]:
    """The reverse outline: one row per segment, projected from the times.

    Mechanical on purpose. The timecodes and the shares come from the document,
    so the only thing the expansion supplies is the verb, and the only thing it
    can get wrong is the verb.
    """
    segments = [
        segment for segment in (data.get("segments") or [])
        if isinstance(segment, Mapping)
    ]
    lines = [OUTLINE_NOTE, "", OUTLINE_RAMP_NOTE, "", AI_DRAFT_MARK, ""]
    if not segments:
        lines.append("（尚無段落）")
        return lines
    for segment, share in zip(segments, segment_shares(segments)):
        try:
            span = "%s - %s" % (
                clock(segment_start(segment)), clock(segment_end(segment))
            )
        except (KeyError, TypeError, ValueError):
            span = "-"
        lines.append(OUTLINE_SEPARATOR.join([
            span, "%d%%" % share, OUTLINE_DRAFT % text_of(segment.get("title")),
        ]))
    return lines


def render_summary_slots() -> str:
    """The reader's two slots, each an unfinished sentence rather than a box."""
    return "\n\n".join([*SUMMARY_SLOTS, SUMMARY_SLOT_NOTE])


def render_verification(takeaways: Sequence[str]) -> List[str]:
    """"我應該記住的 3 件事" as a draft, then the reader's own checklist.

    The three lines are projected from ``takeaways_zh`` rather than invented,
    and they arrive collapsed, because a list that is already open invites
    agreement while a folded one has to be opened first.

    No :data:`AI_DRAFT_MARK` here, and this is the one thing guideline 1.3
    changed about the marker (guideline 1.4 keeps it). The marker means "a model still has to write
    this", and whoever writes that placeholder deletes it -- which is what
    makes `ai_draft_remaining=0` a reachable definition of done. These
    candidates are not waiting on a model: keeping, deleting or rewriting them
    is the reader's work, so marking them would have left the count stuck
    above zero for the one reason that is not a defect.
    """
    lines = ["### 我應該記住的 3 件事", "", CANDIDATE_CALLOUT]
    drafts = list(takeaways[:REMEMBER_COUNT])
    while len(drafts) < REMEMBER_COUNT:
        drafts.append("（素材不足，讀者自行補上或刪除本行）")
    lines.extend("> %d. %s" % (number, text) for number, text in enumerate(drafts, 1))
    lines.extend([
        "",
        "保留／刪除／改寫上面三條是讀者的工作；一個字都沒改，這份筆記就等於沒有人碰過。",
        "",
        "### 自我檢核",
        "",
    ])
    lines.extend("- [ ] %s" % item for item in VERIFICATION_ITEMS)
    return lines


#: What the glossary skeleton says under its heading. The section is the one
#: place a note may carry words the speaker did not say, so it says so first.
GLOSSARY_NOTE = (
    "本節不是講者的內容：每條 1 到 2 句白話解釋，不寫數值閾值、不附文獻，"
    "本文不得引用本節。查不到可靠說明的詞就刪掉那一條；"
    "列自 unverified_terms 的詞尤其如此。"
)

#: Each candidate term's placeholder, until the expansion writes it.
GLOSSARY_ENTRY = "- **%s**：（擴寫時寫 1 到 2 句白話解釋）"

#: Used when the document offers no candidate term at all.
GLOSSARY_FILLER = "- （擴寫時列出本文用到、讀者可能不懂的詞；沒有就刪掉整節）"

#: How many candidates the skeleton lists at most. More than this is a list
#: nobody finishes, and the expansion can always add the term it needs.
GLOSSARY_MAX_TERMS = 15

#: An abbreviation candidate: a capital followed by capitals or digits,
#: optionally hyphenated (CT, MRI, BI-RADS, T2), not inside a longer word.
ABBREVIATION = re.compile(
    r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)*(?![A-Za-z0-9])"
)

#: A template that places the glossary itself.
GLOSSARY_PLACEHOLDER = re.compile(r"\{\{\s*glossary\s*\}\}")


def glossary_candidates(data: Mapping[str, Any]) -> List[str]:
    """Terms the glossary skeleton lists: unverified terms, then abbreviations.

    Abbreviations are read from the text the note is projected from (title,
    summaries, takeaways, segment titles and bullets) in document order, so two
    renders list the same terms in the same order.
    """
    seen: List[str] = []

    def add(term: str) -> None:
        term = term.strip()
        if term and term not in seen:
            seen.append(term)

    for term in strings_of(data.get("unverified_terms")):
        add(term)
    texts: List[str] = [
        text_of(data.get("title")), text_of(data.get("overall_summary_zh"))
    ]
    texts.extend(strings_of(data.get("takeaways_zh")))
    segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        texts.append(text_of(segment.get("title")))
        texts.append(text_of(segment.get("summary_zh")))
        texts.extend(item["text"] for item in bullet_items(segment))
    for text in texts:
        for match in ABBREVIATION.finditer(text):
            add(match.group(0))
    return seen[:GLOSSARY_MAX_TERMS]


def render_glossary(data: Mapping[str, Any]) -> List[str]:
    """The glossary skeleton: heading, what the section is, one line per term."""
    lines = [guideline.GLOSSARY_HEADING, "", GLOSSARY_NOTE, "", AI_DRAFT_MARK, ""]
    terms = glossary_candidates(data)
    if terms:
        lines.extend(GLOSSARY_ENTRY % term for term in terms)
    else:
        lines.append(GLOSSARY_FILLER)
    return lines


def insert_glossary(text: str, glossary: str) -> str:
    """Put the glossary straight before ``### References``.

    A template may place it itself with ``{{glossary}}``; one that does not
    (every overlay written before 1.4) still gets it, in the position the
    guideline fixes. Without a References heading there is nowhere defined to
    put it, and the note is returned as it was.
    """
    lines = text.split("\n")
    for position, line in enumerate(lines):
        if line.strip() == "### References":
            return "\n".join(lines[:position] + [glossary, ""] + lines[position:])
    return text


#: The callout that carries the document's top-level ``editorial_notes_zh``
#: (the four-arc check and similar editorial remarks) inside Summary.
EDITORIAL_CALLOUT = "> [!note]- 編輯備註（四段弧等，非講者原話）"


def render_editorial(data: Mapping[str, Any]) -> List[str]:
    """Top-level ``editorial_notes_zh`` as a collapsed callout, or nothing."""
    notes = [item.strip() for item in strings_of(data.get("editorial_notes_zh"))]
    notes = [item for item in notes if item]
    if not notes:
        return []
    return [EDITORIAL_CALLOUT] + ["> - %s" % item for item in notes]


def render_skeleton(
    data: Mapping[str, Any],
    style: Optional[str] = None,
    stem: Optional[str] = None,
    profile: str = loader.BUILTIN_PROFILE,
    template: Optional[str] = None,
    frontmatter: Optional[str] = None,
    media_base_url: Optional[str] = None,
    media_offset: float = 0.0,
) -> str:
    """The deterministic skeleton note, projected only from *data*.

    ``media_base_url`` turns every printed time into a link that opens the
    video at that second; the folder the video is served from is the caller's
    to know, never this package's.

    No clock is read, no model is called, and nothing outside *data* and the
    profile's template files reaches the output, so rendering the same document
    with the same profile twice produces the same bytes.
    """
    chosen = loader.note_style(profile, style)
    if chosen not in STYLES:
        chosen = loader.DEFAULT_NOTE_STYLE
    body = template if template is not None else loader.note_template(profile)
    if not body:
        raise ValueError(
            "profile %r ships no %s" % (profile, loader.NOTE_TEMPLATE)
        )

    segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    takeaways = strings_of(data.get("takeaways_zh"))

    url = video_url(data, media_base_url, media_offset)
    evergreen = "**「%s」**" % takeaways[0] if takeaways else "**「（待擴寫）」**"
    summary_items = []
    for item in takeaways:
        at = takeaway_anchor(item, segments) if url else None
        summary_items.append("- %s%s" % (item, _timestamp_suffix(at, url) if at is not None else ""))
    summary = "\n".join(summary_items) or "- （待擴寫）"
    editorial = render_editorial(data)
    if editorial:
        summary = "\n".join([summary, ""] + editorial)
    summary = managed("summary", summary)
    reader = loader.note_reader(profile)
    glossary = "\n".join(render_glossary(data)) if reader != "self" else ""

    section_lines: List[str] = []
    for position, segment in enumerate(segments, 1):
        if not isinstance(segment, Mapping):
            continue
        if section_lines:
            section_lines.append("")
        section_lines.extend(render_segment(segment, position, chosen, url))

    context = {
        "frontmatter": render_frontmatter(data, frontmatter, stem, profile),
        "declaration": DRAFT_DECLARATION,
        "summary_slots": render_summary_slots(),
        "speaker_outline": "\n".join(render_speaker_outline(data)),
        "evergreen": evergreen,
        "summary": summary,
        "segments": "\n".join(section_lines) or "（尚無段落）",
        "references": managed("references", "\n".join(render_references(data))),
        "questions": "\n".join(render_questions(data, url)),
        "triage": managed("triage", "\n".join(render_triage(data, url))),
        "verification": "\n".join(render_verification(takeaways)),
        "title": text_of(data.get("title")),
        "glossary": glossary,
    }
    body = body.replace("\r\n", "\n")
    placed = bool(GLOSSARY_PLACEHOLDER.search(body))
    text = fill(body, context)
    if glossary and not placed:
        text = insert_glossary(text, glossary)
    triage = context["triage"]
    if triage and not re.search(r"\{\{\s*triage\s*\}\}", body):
        text = insert_triage(text, triage)
    text = insert_style_marker(text, chosen)
    return text.rstrip("\n") + "\n"


# -- managed blocks and --refresh -------------------------------------------------
# Three blocks of the skeleton are projections of the document that nobody is
# meant to rewrite: the takeaway list in Summary, ## 分流 and the References
# block. They are fenced with invisible comments so `l2n render --refresh` can
# replace exactly them after the JSON changes, and leave every expanded line of
# the note where the writer put it.

MANAGED_BEGIN = "<!-- l2n:begin %s -->"
MANAGED_END = "<!-- l2n:end %s -->"
MANAGED_BLOCKS = ("summary", "triage", "references")


def managed(name: str, body: str) -> str:
    """``body`` fenced as the managed block ``name``; empty stays empty."""
    if not body:
        return ""
    return "%s\n%s\n%s" % (MANAGED_BEGIN % name, body, MANAGED_END % name)


def _block_span(text: str, name: str) -> Optional[Tuple[int, int]]:
    begin, end = MANAGED_BEGIN % name, MANAGED_END % name
    start = text.find(begin)
    stop = text.find(end, start + 1) if start >= 0 else -1
    if start < 0 or stop < 0:
        return None
    return start, stop + len(end)


def _references_span(text: str) -> Optional[Tuple[int, int]]:
    """An unfenced References block: after its heading, up to the next heading."""
    match = re.search(r"(?m)^### References[ \t]*\n", text)
    if not match:
        return None
    following = re.search(r"(?m)^#{1,3} ", text[match.end():])
    stop = match.end() + following.start() if following else len(text)
    return match.end(), stop


def refresh_note(existing: str, fresh: str) -> Tuple[str, List[str], List[str]]:
    """``existing`` with its managed blocks replaced by ``fresh``'s.

    Returns ``(text, replaced, skipped)``. A note written before the blocks
    were fenced still gets its References (found by heading) and gains 分流
    (inserted before the Note body); its Summary list is left alone, because
    without the fence there is no telling where the projection ends and the
    writer's own lines begin.
    """
    text, replaced, skipped = existing, [], []
    for name in MANAGED_BLOCKS:
        source = _block_span(fresh, name)
        block = fresh[source[0]:source[1]] if source else ""
        target = _block_span(text, name)
        if target:
            text = text[:target[0]] + block + text[target[1]:]
            replaced.append(name)
        elif not block:
            continue
        elif name == "triage":
            text = insert_triage(text, block)
            replaced.append(name)
        elif name == "references" and _references_span(text):
            start, stop = _references_span(text)
            text = text[:start] + "\n" + block + "\n\n" + text[stop:].lstrip("\n")
            replaced.append(name)
        else:
            skipped.append(name)
    return text, replaced, skipped


# -- --upgrade: a note written before the current skeleton -------------------------

def _heading_line(lines: Sequence[str], heading: str) -> int:
    for position, line in enumerate(lines):
        if line.strip() == heading:
            return position
    return -1


def _fresh_section(fresh_lines: Sequence[str], heading: str) -> List[str]:
    """One mandatory section of a fresh skeleton: its heading up to the next
    mandatory heading (or the 分流 block, which has its own way in)."""
    start = _heading_line(fresh_lines, heading)
    if start < 0:
        return []
    others = {h for h, _ in guideline.MANDATORY_SECTIONS if h != heading}
    stop = len(fresh_lines)
    for position in range(start + 1, len(fresh_lines)):
        line = fresh_lines[position].strip()
        if line in others or line == MANAGED_BEGIN % "triage":
            stop = position
            break
    section = list(fresh_lines[start:stop])
    while section and not section[-1].strip():
        section.pop()
    return section


def upgrade_note(existing: str, fresh: str, style: str) -> Tuple[str, List[str], List[str]]:
    """Bring a note written to an older skeleton up to the current one.

    Returns ``(text, added, notes)``. Nothing already in the note is removed or
    rewritten: a missing mandatory section is taken from ``fresh`` and put
    where the order says it goes; a References section without the
    correction table gets the fenced table at its top, above what it already
    holds; the style marker is added (``style``); 分流 is added when the
    document has it. Sections that exist but sit out of order are reported,
    not moved -- moving prose is an edit, and edits are the writer's.
    """
    lines = existing.replace("\r\n", "\n").split("\n")
    fresh_lines = fresh.split("\n")
    added: List[str] = []
    notes: List[str] = []
    order = [h for h, _ in guideline.MANDATORY_SECTIONS]
    for index, heading in enumerate(order):
        if _heading_line(lines, heading) >= 0:
            continue
        section = _fresh_section(fresh_lines, heading)
        if not section:
            continue
        following = [_heading_line(lines, h) for h in order[index + 1:]]
        following = [p for p in following if p >= 0]
        at = min(following) if following else len(lines)
        while at == len(lines) and lines and not lines[-1].strip():
            lines.pop()
            at = len(lines)
        block = ([""] if at and lines[at - 1].strip() else []) + section + [""]
        lines[at:at] = block
        added.append(heading)

    seen = [_heading_line(lines, h) for h in order]
    present = [p for p in seen if p >= 0]
    if present != sorted(present):
        notes.append("mandatory sections are out of order; left in place for the writer to move")

    text = "\n".join(lines)
    references_at = _heading_line(lines, "### References")
    if references_at >= 0 and _block_span(text, "references") is None:
        span = _references_span(text)
        body = text[span[0]:span[1]] if span else ""
        if not re.search(r"\|\s*heard\s*\|\s*correct\s*\|", body):
            source = _block_span(fresh, "references")
            if source:
                block = fresh[source[0]:source[1]]
                text = text[:span[0]] + "\n" + block + "\n" + text[span[0]:]
                added.append("References correction table")

    triage = _block_span(fresh, "triage")
    if triage and _block_span(text, "triage") is None and TRIAGE_HEADING not in text:
        text = insert_triage(text, fresh[triage[0]:triage[1]])
        added.append(TRIAGE_HEADING)

    if read_style_marker(text) is None:
        text = insert_style_marker(text, style)
        added.append("style marker (%s)" % style)
    return text.rstrip("\n") + "\n", added, notes


def insert_triage(text: str, triage: str) -> str:
    """Put ``## 分流`` right before ``# Note (layer 1-3)``, or at the end.

    For an overlay template written before 2.1 that has no ``{{triage}}``:
    a note about whether to watch belongs before the notes on what was said.
    """
    marker = "\n# Note (layer 1-3)"
    at = text.find(marker)
    if at < 0:
        return text.rstrip("\n") + "\n\n" + triage + "\n"
    return text[:at] + "\n" + triage + "\n" + text[at:]


def write_skeleton(path: Path, data: Mapping[str, Any], **kwargs: Any) -> Path:
    """Write the skeleton as UTF-8 without a BOM and with LF endings."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        render_skeleton(data, **kwargs), encoding="utf-8", newline="\n"
    )
    return destination


__all__ = [
    "AI_DRAFT_MARK",
    "ABBREVIATION",
    "EDITORIAL_CALLOUT",
    "GLOSSARY_ENTRY",
    "GLOSSARY_FILLER",
    "GLOSSARY_MAX_TERMS",
    "GLOSSARY_NOTE",
    "GLOSSARY_PLACEHOLDER",
    "glossary_candidates",
    "insert_glossary",
    "render_editorial",
    "render_glossary",
    "STYLE_MARKER",
    "STYLE_MARKER_RE",
    "insert_style_marker",
    "read_style_marker",
    "style_marker",
    "CANDIDATE_CALLOUT",
    "DRAFT_DECLARATION",
    "SUMMARY_SLOTS",
    "SUMMARY_SLOT_NOTE",
    "ANSWER_INDENT",
    "ANSWER_PLACEHOLDER",
    "CORRECTION_COLUMNS",
    "PLACEHOLDER",
    "OUTLINE_DRAFT",
    "OUTLINE_NOTE",
    "OUTLINE_RAMP_NOTE",
    "OUTLINE_SEPARATOR",
    "QUESTION_FILLER",
    "QUESTION_NOTE",
    "REMEMBER_COUNT",
    "STYLES",
    "UNVERIFIED_MARK",
    "VERIFICATION_ITEMS",
    "bullet_items",
    "bullet_texts",
    "clock",
    "explicit_questions",
    "fill",
    "frontmatter_block",
    "frontmatter_context",
    "frontmatter_keys",
    "question_drafts",
    "quote_items",
    "render_frontmatter",
    "render_note",
    "render_questions",
    "render_references",
    "render_segment",
    "render_skeleton",
    "render_triage",
    "refresh_note",
    "upgrade_note",
    "managed",
    "MANAGED_BEGIN",
    "MANAGED_END",
    "MANAGED_BLOCKS",
    "insert_triage",
    "segment_questions",
    "short_clock",
    "takeaway_anchor",
    "time_link",
    "video_url",
    "MediaLink",
    "TIME_LINK",
    "TRIAGE_HEADING",
    "SEGMENT_QUESTION_MIN",
    "KNOWN_LINE",
    "render_speaker_outline",
    "render_summary_slots",
    "render_verification",
    "segment_shares",
    "segment_titles",
    "strings_of",
    "text_of",
    "write_note",
    "write_skeleton",
]
