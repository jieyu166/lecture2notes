"""The triage brief: ``l2n triage-prompt <stem>.json --vault <dir>``.

Triage answers the question a reader has *before* watching: which segments
are new to me, which ones my notes already cover, and how much time can I
save. The judgement is the model's; what a program can do is find, for every
segment, the notes in the reader's vault that might already cover it, so the
model opens those instead of guessing.

The vault is read on this machine and nothing is written. Keyword hits are
candidates, not verdicts: a note that shares three terms with a segment may
still say nothing about the segment's point, which is why the brief tells the
model to open each candidate before calling a segment 已知.

Deterministic: the same document and vault give the same brief.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from lecture2notes.schema.model import TRIAGE_NOVELTY, TRIAGE_VERDICTS

#: How many keywords a segment is searched by.
KEYWORDS_PER_SEGMENT = 10
#: How much of a matching note line the brief quotes back.
SNIPPET_CHARS = 80
#: A keyword in a note's name or title counts this many times a body hit.
TITLE_WEIGHT = 3
#: A candidate needs this score: one title hit, or several body hits.
MIN_SCORE = 3

#: An English term: a word of three or more characters, or an acronym such as
#: CT, T2 or BI-RADS.
ENGLISH_TERM = re.compile(r"[A-Za-z][A-Za-z0-9+\-]*[A-Za-z0-9]")
#: A run of CJK characters, the unit a Chinese title is cut into.
CJK_RUN = re.compile(r"[一-鿿]{2,12}")
#: Words that match everything and so tell nothing.
STOPWORDS = frozenset("""
the and for with from that this these those are was were has have had not but
can may will its into over under than then there their about which what when
how why who all any one two use used using case cases also more most such
""".split())
#: Chinese chunks that are structure, not topic.
CJK_STOP = frozenset(("介紹", "總結", "結論", "概論", "簡介", "問答", "開場", "回顧", "案例", "討論"))

#: L3 in the frontmatter tags, in either YAML list spelling.
L3_TAG = re.compile(
    r"(?m)^\s*-\s*[\"']?L3[\"']?\s*$|^tags:\s*\[[^\]]*\bL3\b[^\]]*\]"
)
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
FRONTMATTER_TITLE = re.compile(r"(?m)^title:\s*[\"']?(.+?)[\"']?\s*$")


@dataclass(frozen=True)
class VaultNote:
    """One Markdown note, as much of it as matching needs."""

    name: str
    rel: str
    title: str
    body: str
    is_l3: bool

    @property
    def link(self) -> str:
        return "[[%s]]" % self.name


@dataclass(frozen=True)
class Candidate:
    note: VaultNote
    score: int
    matched: Tuple[str, ...]
    snippet: str


def _segments(data: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [s for s in (data.get("segments") or []) if isinstance(s, Mapping)]


def _clock(seconds: Any) -> str:
    total = max(int(float(seconds or 0)), 0)
    hours, rest = divmod(total, 3600)
    if hours:
        return "%d:%02d:%02d" % (hours, rest // 60, rest % 60)
    return "%02d:%02d" % (rest // 60, rest % 60)


def _minutes(segment: Mapping[str, Any]) -> float:
    try:
        return (float(segment.get("end_sec")) - float(segment.get("start_sec"))) / 60.0
    except (TypeError, ValueError):
        return 0.0


def _bullet_texts(segment: Mapping[str, Any]) -> List[str]:
    out = []
    for bullet in segment.get("bullets_zh") or []:
        text = bullet.get("text") if isinstance(bullet, Mapping) else bullet
        if isinstance(text, str):
            out.append(text)
    return out


def segment_keywords(segment: Mapping[str, Any], limit: int = KEYWORDS_PER_SEGMENT) -> List[str]:
    """The terms a segment is searched by, most telling first.

    English terms and acronyms from the title (weighted), summary and bullets;
    Chinese runs from the title only, because without a word segmenter a run
    cut out of a sentence is more often a fragment than a term.
    """
    title = str(segment.get("title") or "")
    body = " ".join([str(segment.get("summary_zh") or "")] + _bullet_texts(segment))
    counts: Counter = Counter()
    spelling: Dict[str, str] = {}
    for text, weight in ((title, TITLE_WEIGHT), (body, 1)):
        for match in ENGLISH_TERM.finditer(text):
            word = match.group(0)
            key = word.lower()
            if key in STOPWORDS or (len(word) < 3 and not word.isupper()):
                continue
            counts[key] += weight
            spelling.setdefault(key, word)
    for run in CJK_RUN.findall(title):
        if run not in CJK_STOP:
            counts[run] += TITLE_WEIGHT
            spelling.setdefault(run, run)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [spelling[key] for key, _ in ranked[:limit]]


def _read_note(path: Path, vault: Path) -> Optional[VaultNote]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    text = text.lstrip("﻿").replace("\r\n", "\n")
    match = FRONTMATTER.match(text)
    front = match.group(1) if match else ""
    body = text[match.end():] if match else text
    title_match = FRONTMATTER_TITLE.search(front)
    return VaultNote(
        name=path.stem,
        rel=path.relative_to(vault).as_posix(),
        title=title_match.group(1).strip() if title_match else path.stem,
        body=body,
        is_l3=bool(L3_TAG.search(front)),
    )


def scan_vault(vault: Path, only_l3: bool = True) -> List[VaultNote]:
    """Every note in ``vault`` (hidden folders skipped), L3 notes only by default."""
    vault = Path(vault)
    notes: List[VaultNote] = []
    for path in sorted(vault.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(vault).parts):
            continue
        note = _read_note(path, vault)
        if note is not None and (note.is_l3 or not only_l3):
            notes.append(note)
    return notes


def _snippet(body: str, term: str) -> str:
    needle = term.lower()
    for line in body.split("\n"):
        if needle in line.lower():
            line = " ".join(line.split())
            return line if len(line) <= SNIPPET_CHARS else line[:SNIPPET_CHARS] + "..."
    return ""


def candidates(
    keywords: Sequence[str], notes: Iterable[VaultNote], top: int = 5
) -> List[Candidate]:
    """The notes most likely to cover a segment, best first."""
    found: List[Candidate] = []
    for note in notes:
        heading = (note.name + " " + note.title).lower()
        body = note.body.lower()
        score, matched = 0, []
        for keyword in keywords:
            key = keyword.lower()
            if key in heading:
                score += TITLE_WEIGHT
                matched.append(keyword)
            elif key in body:
                score += 1
                matched.append(keyword)
        if score >= MIN_SCORE:
            body_hit = next((k for k in matched if k.lower() in body), "")
            found.append(Candidate(
                note, score, tuple(matched), _snippet(note.body, body_hit) if body_hit else ""
            ))
    found.sort(key=lambda c: (-c.score, c.note.rel))
    return found[:max(top, 0)]


def triage_prompt(
    json_path: Path,
    data: Mapping[str, Any],
    notes: Sequence[VaultNote],
    top: int = 5,
) -> str:
    """The whole brief, as Markdown."""
    segments = _segments(data)
    total = sum(_minutes(s) for s in segments)
    out: List[str] = [
        "# 分流交辦書：%s" % (data.get("title") or Path(json_path).stem),
        "",
        "- 文件：`%s`" % Path(json_path).name,
        "- 全片 %.1f 分鐘，%d 段；比對了 %d 篇筆記" % (total, len(segments), len(notes)),
        "",
        "## 你要做的事",
        "",
        "對每一段寫 `segments[].triage`，最後寫頂層 `triage_overall`，再跑 `l2n check json`。",
        "",
        "1. `novelty` 只能是 %s：" % "／".join(TRIAGE_NOVELTY),
        "   - **新知**：vault 沒有筆記涵蓋本段的主要判準、數值或做法。",
        "   - **已知**：已有筆記涵蓋本段核心內容。`related_notes` 必填那篇筆記的 `[[連結]]`。",
        "     下面的候選只是關鍵字命中，**打開筆記確認內容真的涵蓋本段**才算，同名不算。",
        "   - **複習**：vault 有相關筆記，但本段有新數值、新分類或講者經驗，值得對照著看。",
        "   - `reason`：一句話，寫出涵蓋或沒涵蓋的具體內容（哪個判準、哪個數值）。",
        "2. `triage_overall`：",
        "   - `verdict` 只能是 %s。" % "／".join(TRIAGE_VERDICTS),
        "   - `must_watch`：必看段的 `index`；`est_minutes`：這些段的時長加總（見下方各段分鐘數）。",
        "   - `reason` 要具體到能照做：**哪幾段必看（寫「第 N 段」）、省下幾分鐘、為什麼**。",
        "     例：「只需看第 3、5 段（共 14 分鐘，省下 31 分鐘）：第 3 段的新分級與第 5 段的追蹤間隔",
        "     vault 沒有；其餘各段已由 [[某筆記]] 涵蓋。」泛泛的「內容實用、值得一看」會被 check 擋下。",
        "3. 不得編造：判斷只能根據本段內容與你打開的筆記。",
        "",
        "## 各段",
        "",
    ]
    for segment in segments:
        keywords = segment_keywords(segment)
        out.append("### 第 %s 段 %s–%s（%.1f 分鐘）%s" % (
            segment.get("index"), _clock(segment.get("start_sec")),
            _clock(segment.get("end_sec")), _minutes(segment), segment.get("title") or "",
        ))
        out.append("")
        summary = str(segment.get("summary_zh") or "").strip()
        if summary:
            out.append("- 摘要：%s" % summary)
        out.append("- 關鍵字：%s" % ("、".join(keywords) if keywords else "（無）"))
        hits = candidates(keywords, notes, top=top)
        if not hits:
            out.append("- 候選筆記：vault 中沒有命中，傾向新知（仍需你判斷）")
        else:
            out.append("- 候選筆記：")
            for hit in hits:
                line = "  - %s `%s`（分數 %d，命中：%s）" % (
                    hit.note.link, hit.note.rel, hit.score, "、".join(hit.matched)
                )
                if hit.snippet:
                    line += "——「%s」" % hit.snippet
                out.append(line)
        out.append("")
    out += [
        "## 寫回的形狀",
        "",
        "```json",
        '"triage_overall": {"verdict": "只看新知段", "must_watch": [3, 5], "est_minutes": 14,',
        '                   "reason": "只需看第 3、5 段（共 14 分鐘，省下 31 分鐘）：……"},',
        '"segments": [{"index": 1, "...": "...",',
        '  "triage": {"novelty": "已知", "reason": "……", "related_notes": ["[[某筆記]]"]}}]',
        "```",
        "",
    ]
    return "\n".join(out)


__all__ = [
    "Candidate",
    "VaultNote",
    "candidates",
    "scan_vault",
    "segment_keywords",
    "triage_prompt",
]
