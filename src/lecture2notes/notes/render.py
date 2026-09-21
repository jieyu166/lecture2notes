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
from typing import Any, Dict, List, Mapping, Optional, Sequence

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

#: Marks the three things the model drafted for the reader to keep, delete or
#: rewrite. It survives in the file, so a note whose marker is still untouched is
#: measurably one that nobody has read. Defined in `schema.model` because the
#: scaffold stage writes the same marker into the JSON; re-exported here under
#: the name every caller in this package already uses.
AI_DRAFT_MARK = _AI_DRAFT_MARK

#: How many candidates the model drafts under "我應該記住的 3 件事".
REMEMBER_COUNT = 3

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


def _timestamp_suffix(value: Any) -> str:
    stamp = clock(value)
    return " (%s)" % stamp if stamp else ""


def render_segment(
    segment: Mapping[str, Any], position: int, style: str = "concise"
) -> List[str]:
    """One ``## n、title`` section: embed, summary, quotes, bullets, in order."""
    lines = ["## %d、%s" % (position, text_of(segment.get("title")))]
    span = "%s - %s" % (
        clock(segment.get("start_sec")), clock(segment.get("end_sec"))
    )
    if span.strip(" -"):
        lines.extend(["", "(%s)" % span])

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
            "", "> 「%s」%s" % (quote["text"], _timestamp_suffix(quote.get("t")))
        ])

    bullets = bullet_items(segment)
    if style == "concise":
        bullets = [item for item in bullets if item["kind"] != "quote"]
    if bullets:
        lines.append("")
        for bullet in bullets:
            if bullet["kind"] == "quote":
                lines.append(
                    "- 「%s」%s" % (bullet["text"], _timestamp_suffix(bullet.get("t")))
                )
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


def render_questions(data: Mapping[str, Any]) -> List[str]:
    """The 題目 section: drafted questions, each with a collapsed answer."""
    lines: List[str] = [QUESTION_NOTE, "", AI_DRAFT_MARK, ""]
    for number, question in enumerate(question_drafts(data), 1):
        if number > 1:
            lines.append("")
        lines.append("%d. %s" % (number, question))
        lines.append("%s%s 答案" % (ANSWER_INDENT, guideline.ANSWER_CALLOUT))
        lines.append("%s> %s" % (ANSWER_INDENT, ANSWER_PLACEHOLDER))
    return lines


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

    The three lines are projected from ``takeaways_zh`` rather than invented, and
    they are fenced by :data:`AI_DRAFT_MARK` because the reader's only job here
    is to keep, delete or rewrite them. A note whose marker is still in place is
    one that nobody has read, and that is worth being able to count.
    """
    lines = ["### 我應該記住的 3 件事", "", AI_DRAFT_MARK, "", CANDIDATE_CALLOUT]
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


def render_skeleton(
    data: Mapping[str, Any],
    style: Optional[str] = None,
    stem: Optional[str] = None,
    profile: str = loader.BUILTIN_PROFILE,
    template: Optional[str] = None,
    frontmatter: Optional[str] = None,
) -> str:
    """The deterministic skeleton note, projected only from *data*.

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

    evergreen = "**「%s」**" % takeaways[0] if takeaways else "**「（待擴寫）」**"
    summary = "\n".join("- %s" % item for item in takeaways) or "- （待擴寫）"

    section_lines: List[str] = []
    for position, segment in enumerate(segments, 1):
        if not isinstance(segment, Mapping):
            continue
        if section_lines:
            section_lines.append("")
        section_lines.extend(render_segment(segment, position, chosen))

    context = {
        "frontmatter": render_frontmatter(data, frontmatter, stem, profile),
        "declaration": DRAFT_DECLARATION,
        "summary_slots": render_summary_slots(),
        "speaker_outline": "\n".join(render_speaker_outline(data)),
        "evergreen": evergreen,
        "summary": summary,
        "segments": "\n".join(section_lines) or "（尚無段落）",
        "references": "\n".join(render_references(data)),
        "questions": "\n".join(render_questions(data)),
        "verification": "\n".join(render_verification(takeaways)),
        "title": text_of(data.get("title")),
    }
    text = fill(body.replace("\r\n", "\n"), context)
    return text.rstrip("\n") + "\n"


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
