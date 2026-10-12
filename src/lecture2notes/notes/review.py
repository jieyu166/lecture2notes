"""The independent-review bundle: ``l2n review-prompt <stem>.json``.

`check note` decides what a program can decide. What it cannot -- a speaker's
"usually" written as "always", a cause the speaker never stated, a reading
order the note invented -- needs somebody who did not write the note to go
back to the sources. This module prints the brief for that person or agent,
the same way ``render --expand-prompt`` prints the brief for the writer.

It is a list of candidates, not a verdict. Every line it prints is something
to look at; none of it is a finding, and nothing here changes an exit code
beyond "the inputs were not there".

Deterministic: the same note, document and transcript give the same bundle,
so two reviewers handed it see the same sample.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from lecture2notes.acceptance import check
from lecture2notes.notes import guideline, render

#: How many 「」 quotations the bundle samples at least (all of them when
#: fewer exist). The independent check this mirrors asked for eight figure
#: callouts; a quotation is the lecture's equivalent of a figure.
QUOTE_SAMPLE = 8

#: How many numbers the bundle lists at most, so a table-heavy note does not
#: turn the brief into a dump.
NUMBER_LIMIT = 40

#: How much of a line a candidate quotes back.
EXCERPT_CHARS = 60

#: Timecodes and clock values are coordinates, not claims; they are removed
#: before a line is searched for numbers.
CLOCK = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")

#: A number worth checking against the source, with its unit when it has one.
NUMBER = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|mm|cm|HU|ms|kg|歲|倍|分鐘|秒|年|週|個月|例|位|次)?"
)

#: A 「」 quotation, with the timecode that follows it when there is one.
QUOTATION = re.compile(r"「([^」]+)」\s*(?:\(\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\))?")

#: The English age expression a decade conversion starts from.
DECADE = re.compile(r"\bdecades?\b", re.IGNORECASE)

#: A numbered segment heading's own number, which is structure, not a claim.
SEGMENT_NUMBER = re.compile(r"^\s*##\s*\d+[、.]")


def _excerpt(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= EXCERPT_CHARS:
        return text
    return text[:EXCERPT_CHARS] + "..."


def _segments(data: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [s for s in (data.get("segments") or []) if isinstance(s, Mapping)]


def _span(segment: Mapping[str, Any]) -> str:
    return "%s - %s" % (
        render.clock(segment.get("start_sec")), render.clock(segment.get("end_sec"))
    )


def number_candidates(
    lines: Sequence[str], data: Mapping[str, Any]
) -> List[str]:
    """Every Note-body line that states a number, with its segment's time span."""
    regions = check.note_regions(lines)
    if "note" not in regions:
        return []
    segments = _segments(data)
    out: List[str] = []
    for order, (title, start, end) in enumerate(
        check.segment_sections(lines, regions["note"])
    ):
        span = _span(segments[order]) if order < len(segments) else "?"
        for position in range(start, end):
            line = lines[position]
            body = line.strip()
            if not body or body.startswith("![[") or SEGMENT_NUMBER.match(line):
                continue
            if check.TIMESPAN_LINE.match(body):
                continue
            stripped = CLOCK.sub("", body)
            found = [m.group(0).strip() for m in NUMBER.finditer(stripped)]
            if not found:
                continue
            out.append("- 段 %d（%s）L%d [%s]: %s" % (
                order + 1, span, position + 1, ", ".join(found), _excerpt(body)
            ))
            if len(out) >= NUMBER_LIMIT:
                return out
    return out


def decade_candidates(
    lines: Sequence[str], data: Mapping[str, Any], transcript: Optional[Path]
) -> List[str]:
    """Transcript cues and note lines that mention a decade of age."""
    out: List[str] = []
    segments = _segments(data)
    if transcript is not None and Path(transcript).is_file():
        from lecture2notes.engines.base import parse_srt_text, read_subtitle_text

        try:
            cues = parse_srt_text(read_subtitle_text(Path(transcript)))
        except (OSError, UnicodeDecodeError, ValueError):
            cues = []
        for cue in cues:
            if not DECADE.search(cue.text):
                continue
            number = next(
                (
                    index + 1 for index, segment in enumerate(segments)
                    if float(segment.get("start_sec") or 0) <= cue.start
                    < float(segment.get("end_sec") or 0)
                ),
                0,
            )
            where = "段 %d" % number if number else "段 ?"
            out.append("- 逐字稿 %s（%s）: %s" % (
                where, render.clock(cue.start), _excerpt(cue.text)
            ))
    for position, line in enumerate(lines):
        if DECADE.search(line):
            out.append("- 筆記 L%d: %s" % (position + 1, _excerpt(line.strip())))
    return out


def quotation_sample(lines: Sequence[str]) -> Tuple[int, List[str]]:
    """``(total, sampled lines)``: evenly spaced 「」 quotations from the note.

    References and the frontmatter are left out: the correction table quotes
    wrong spellings on purpose, and neither is the speaker's words.
    """
    regions = check.note_regions(lines)
    skip = set(range(*regions["references"])) if "references" in regions else set()
    found: List[str] = []
    for position in range(check.frontmatter_end(lines), len(lines)):
        if position in skip:
            continue
        for match in QUOTATION.finditer(lines[position]):
            stamp = match.group(2) or "無時間碼"
            found.append("- L%d (%s): 「%s」" % (
                position + 1, stamp, _excerpt(match.group(1))
            ))
    total = len(found)
    if total <= QUOTE_SAMPLE:
        return total, found
    size = min(total, max(QUOTE_SAMPLE, int(math.ceil(total / 5.0))))
    step = total / float(size)
    picked = sorted({int(index * step) for index in range(size)})
    return total, [found[index] for index in picked]


def context_hits(
    lines: Sequence[str], data: Mapping[str, Any], profile: str,
    transcript: Optional[Path],
) -> List[str]:
    """R12's context-layer hits: right only if the reviewer agrees."""
    out: List[str] = []
    for hit in check.forbidden_term_hits(lines, data, profile, transcript):
        if hit.layer != "context":
            continue
        out.append("- L%d: %s | %s" % (
            hit.line + 1, hit.message(), _excerpt(lines[hit.line].strip())
        ))
    return out


REPORT_FORMAT = (
    "第一行只寫 `PASS` 或 `需修正`。",
    "需修正時，列一張修正清單，**依嚴重度排序**（會讓讀者學錯的排最前）；每一條都要有："
    "行號、原字串（逐字，供 grep）、建議改法、來源時間碼（講義則附頁碼）與關鍵詞。",
    "只列你回到來源確認過的錯；沒把握的另列「待撰寫者確認」，不混在修正清單裡。",
)


def review_prompt(
    json_path: Path,
    note_path: Path,
    data: Mapping[str, Any],
    note_text: str,
    profile: str,
    transcript_path: Optional[Path] = None,
) -> str:
    """The independent reviewer's brief for one note, as one block of text."""
    json_path = Path(json_path)
    note_path = Path(note_path)
    transcript = Path(transcript_path) if transcript_path else json_path.with_name(
        json_path.stem + ".srt"
    )
    lines = check.note_lines(note_text)

    parts: List[str] = [
        guideline.VERSION_LINE,
        "",
        "# 獨立驗收任務",
        "",
        "你沒有參與這份筆記的撰寫。這是唯讀工作：不改筆記、不改 JSON；"
        "自己回到逐字稿與講義抽來源，自己寫檢查方法，不沿用撰寫者的草稿或腳本。"
        "修正清單交回撰寫者，由撰寫者改。",
        "",
        "## 要讀的檔案",
        "",
        "1. 筆記（只讀）: %s" % note_path,
        "2. 逐字稿: %s%s" % (transcript, "" if transcript.is_file() else "（找不到）"),
        "3. 正式 JSON: %s" % json_path,
        "4. 官方講義（若有，優先序高於逐字稿）",
        "",
        "先跑一次機器驗收，它抓得到的不必你重抓：",
        "",
        "l2n check note %s --note %s" % (json_path, note_path),
        "",
        "## 機器抓不到的錯誤（逐項找，規範 2.5）",
        "",
    ]
    parts.extend("- **%s**：%s" % (name, what) for name, what in guideline.REVIEW_ERRORS)

    parts.extend(["", "## 抽查候選（機器列出位置，對錯由你判斷）", ""])
    parts.append("### 數字：回到所屬段落的時間區間核對")
    parts.append("")
    numbers = number_candidates(lines, data)
    parts.extend(numbers or ["- （Note 本文沒有數字）"])

    parts.extend(["", "### decade：年齡換算要附原文序數", ""])
    decades = decade_candidates(lines, data, transcript)
    parts.extend(decades or ["- （逐字稿與筆記都沒有 decade）"])

    total, quotes = quotation_sample(lines)
    parts.extend([
        "",
        "### 「」引文抽樣（共 %d 句，抽 %d 句）：逐句回逐字稿對時間碼與字面" % (
            total, len(quotes)
        ),
        "",
    ])
    parts.extend(quotes or ["- （筆記沒有「」引文）"])

    parts.extend(["", "### R12 context 層命中：英文線索在，但要你判斷是不是誤譯", ""])
    hits = context_hits(lines, data, profile, transcript)
    parts.extend(hits or ["- （無）"])

    parts.extend(["", "## 回報格式", ""])
    parts.extend("- %s" % item for item in REPORT_FORMAT)
    parts.extend([
        "",
        "## 修正之後",
        "",
        "撰寫者改完，主對話用 grep 抽查修正清單裡的每一個原字串確實消失，"
        "再跑一次 `l2n check note`。",
    ])
    return "\n".join(parts) + "\n"


__all__ = [
    "EXCERPT_CHARS",
    "NUMBER_LIMIT",
    "QUOTE_SAMPLE",
    "REPORT_FORMAT",
    "context_hits",
    "decade_candidates",
    "number_candidates",
    "quotation_sample",
    "review_prompt",
]
