"""The note-writing guideline as the code sees it.

``docs/note-writing-guideline.md`` is the document a person reads; this module is
the part of it a program needs: the version string, the section names a note must
carry, and the rule identifiers the check reports. The version lives here and
nowhere else -- the document quotes it, and a test asserts the two agree -- so
there is exactly one place to bump when the rules change.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from lecture2notes import resources

#: Bump this whenever a rule changes meaning. `l2n check note` prints it, so a
#: report always says which edition of the rules produced it.
GUIDELINE_VERSION = "1.3"

#: The exact line the document carries and the check prints.
VERSION_LINE = "guideline_version: %s" % GUIDELINE_VERSION

#: Where the document lives, relative to the repository root.
GUIDELINE_DOC = "docs/note-writing-guideline.md"

#: The two parts the document must keep separate, named the same in both places.
MUST_FOLLOW_HEADING = "LLM 必須遵守"
MACHINE_CHECKABLE_HEADING = "機器可檢查"

#: Mandatory sections, in the order a note must carry them (rule R1). Each entry
#: is (heading line as rendered, short name used in findings).
MANDATORY_SECTIONS: Tuple[Tuple[str, str], ...] = (
    ("# Evergreen Note", "Evergreen Note"),
    ("# Summary", "Summary"),
    ("## 講者骨架", "講者骨架"),
    ("# Note (layer 1-3)", "Note (layer 1-3)"),
    ("### References", "References"),
    ("## 題目", "題目"),
    ("## 學習驗證", "學習驗證"),
)

#: A run at least this long (whitespace and punctuation removed) that also
#: appears in the transcript, and is not marked as a quotation, is R5.
TRANSCRIPT_RUN_CHARS = 40

#: A sentence at least this long (same normalisation) that appears in two
#: different sections of the note is R11. Shorter than R5's run because this
#: is not about pasting a transcript: twenty-five identical characters in two
#: places is a sentence somebody copied, not a coincidence.
DUPLICATE_SENTENCE_CHARS = 25

#: How many questions the 題目 section must carry (rule R9), and how many the
#: skeleton drafts at most. Recall is answering, not rereading, so a note whose
#: question section is empty has skipped the only step that makes it stick.
MIN_QUESTIONS = 3
MAX_QUESTIONS = 5

#: The one question that cannot be answered by scanning the note. Half-width
#: square brackets around the two characters, at the very start of the question
#: text -- R9 accepts that spelling and no other, because a marker that may sit
#: anywhere is one a reader stops seeing and a writer stops placing.
INFERENCE_MARK = "[推論]"

#: Printed by R9 when no question carries the marker, so the finding says what
#: to write rather than only what is missing.
INFERENCE_PREFIX_HINT = "1. %s 若某個案例具備 A 但缺少 B，這篇的結論還成立嗎？"

#: Answers live behind a collapsed callout, so the reader answers before seeing.
ANSWER_CALLOUT = "> [!answer]-"

#: How much of the offending run a finding quotes back.
FINDING_EXCERPT_CHARS = 20

#: What `l2n render` already put in each section, and what is left to write.
#: A field run expanded the Note body, left Evergreen and the answers as the
#: renderer wrote them, and had no way to know it had stopped early -- the
#: bundle told it to "expand the skeleton" without saying which parts of the
#: skeleton were already finished. Each row is (section, landed, to do).
EXPANSION_CHECKLIST: Tuple[Tuple[str, str, str], ...] = (
    (
        "# Evergreen Note",
        "takeaways_zh 的第一條，包成一句粗體引文",
        "**先寫這一個**：換成**一句話總結本課程內容**，指得回時間碼；"
        "不是 takeaways_zh[0] 照抄，也不是半句逐字稿。自己做的歸納或排序標"
        "「（本筆記整理）」（正反例見規範 4.1）",
    ),
    (
        "# Summary",
        "已經逐條引用 takeaways_zh，不必重寫",
        "兩個句首空槽（開這篇之前我卡在___／這篇沒回答到的是___）是讀者的，"
        "**不要代填，也不要刪**",
    ),
    (
        "## 講者骨架",
        "每段一行：時間區間、佔全片比例，第三欄是 ai-draft 佔位",
        "待改寫：第三欄換成動詞開頭一句，只寫講者做了什麼，不寫他講了什麼；"
        "寫完把該節的 `<!-- ai-draft -->` 一起刪掉",
    ),
    (
        "# Note (layer 1-3)",
        "每段的標題、時間、影格嵌入、summary_zh、引用與條列",
        "待擴寫：每一段以推理鏈重述（講者為什麼這樣說），不是把條列擴寫成句子",
    ),
    (
        "### References",
        "corrections 表、unverified_terms、source 三塊都已填",
        "通常不動；本文改對的 ASR 錯字要在 corrections 表補一列",
    ),
    (
        "## 題目",
        "3 到 5 題草稿，每題下面一個收合的答案 callout",
        "答案待寫（附時間碼）；題目不合用就改寫，至少一題以 %s 開頭；"
        "寫完把該節的 `<!-- ai-draft -->` 一起刪掉",
    ),
    (
        "## 學習驗證",
        "「我應該記住的 3 件事」候選已由 takeaways 投影，收在摺疊 callout 裡"
        "（**不帶** ai-draft 標記）",
        "候選可以改寫得更好，但不要和 Summary 逐字相同（R11）；"
        "保留／刪除是讀者的工作，模型不代勞",
    ),
)

#: The one thing to write before anything else, said in the bundle's own voice.
#: The blind review found the same failure in two independent expansions and in
#: an earlier Claude run: Evergreen left as `takeaways_zh[0]`, which is a single
#: week's number rather than something that survives the lecture. It is first on
#: the checklist because writing it first gives the rest of the note an axis;
#: written last it is only ever the skeleton line with a word changed.
EVERGREEN_FIRST = (
    "**第一件事：先寫 Evergreen。** Evergreen 是一句話總結本課程內容，"
    "而且指得回時間碼；不是 takeaways_zh[0] 照抄，也不是半句逐字稿。"
    "講者沒說、你自己做的歸納或排序，一律標「（本筆記整理）」。"
    "正反例見規範 4.1。R10 通過不代表 Evergreen 合格。"
)


def expansion_checklist(style: str = "concise") -> List[str]:
    """The bundle's "already landed / still yours" table, as lines."""
    lines = [
        "## 已由 render 落地／待你擴寫",
        "",
        EVERGREEN_FIRST,
        "",
        "| 章節 | render 已經落地 | 待你處理 |",
        "| ---- | ---- | ---- |",
    ]
    for section, landed, todo in EXPANSION_CHECKLIST:
        if "%s" in todo:
            todo = todo % INFERENCE_MARK
        lines.append("| `%s` | %s | %s |" % (section, landed, todo))
    lines.extend([
        "",
        "完成的定義不是「check 沒報錯」，而是這兩個數字都歸零：",
        "`note: unexpanded_segments=0/N` 與 `note: ai_draft_remaining=0`。",
        "`ai-draft` 標記只在「還等著你寫」的地方（講者骨架第三欄、題目答案）；"
        "寫完該處就連標記一起刪掉。讀者的兩個空槽與「模型候選」不帶標記，不要去加。",
    ])
    return lines

#: Rule identifiers, so a typo in a finding fails a test rather than a reader.
RULES: Tuple[str, ...] = (
    "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11",
    "R12", "R13", "R14",
)

#: The one section allowed to hold content the speaker did not give: a short
#: plain-language explanation of a term. Its heading says so, and it sits just
#: before ``### References``. Rendered only when ``note.reader`` is not
#: ``self``; required for ``student`` and ``public`` (R14). R3, R5, R10 and
#: R11 do not scan it.
GLOSSARY_HEADING = "## 名詞小辭典（非講者內容）"

#: The radiology reading section. R5, R8 and R13 scan it from this heading to
#: the next level-one heading, the glossary or ``### References``.
READING_HEADING = "# 閱片"

#: The one placeholder R8 accepts, and only inside the reading section: "I
#: looked and the speaker did not cover this", for a fixed callout field. A
#: Note section with nothing to say is deleted instead, so there it is still
#: reported.
NOT_COVERED_NOTE = "N/A（原文未涵蓋）"

#: Words R13 reports in a frame description, outside 「」.
SPECULATION_WORDS: Tuple[str, ...] = ("推測", "推斷")

#: The two markers an inference in the Note body may carry, at the start of
#: the sentence or list item. An inference without one is fabrication.
BODY_INFERENCE_MARKS: Tuple[str, ...] = ("[推論]", "[本筆記整理]")

#: Reader levels, as the guideline's table states them:
#: (level, who, terms and abbreviations, glossary, question type).
READER_GUIDANCE: Tuple[Tuple[str, str, str, str, str], ...] = (
    (
        "self", "筆記主人",
        "專有名詞與縮寫保留英文原文（HARD RULE 6），不另加中譯",
        "不產生",
        "問理由與邊界，至少一題 [推論]",
    ),
    (
        "clerk", "專業入門者",
        "首見寫「中文（English, 縮寫）」，之後用縮寫；分級系統名（BI-RADS 等）例外",
        "選用",
        "同 self，可加一題「把講者的判準套到另一個案例」",
    ),
    (
        "student", "國高中生",
        "首見寫「中文（English, 縮寫）」，縮寫一律展開；能用中文說的就用中文",
        "必須有",
        "先問「講者說了什麼、為什麼」，再一題 [推論]；不考專業細節的記憶",
    ),
    (
        "public", "一般大眾",
        "同 student，而且英文只放在首見的括號裡",
        "必須有",
        "生活情境題，至少一題 [推論]；不用術語出題",
    ),
)


def reader_guidance(reader: str) -> List[str]:
    """The bundle's lines for one reader level."""
    for level, who, terms, glossary, questions in READER_GUIDANCE:
        if level != reader:
            continue
        lines = [
            "## 讀者層級：%s（%s）" % (level, who),
            "",
            "- 詞彙與縮寫：%s" % terms,
            "- 名詞小辭典：%s" % glossary,
            "- 題目型態：%s" % questions,
        ]
        if level != "self":
            lines.extend([
                "- 小辭典固定標題 `%s`，放在 `### References` 之前；每條 1 到 2 句、"
                "不寫數值閾值、不附文獻、本文不得引用它。" % GLOSSARY_HEADING,
                "- 讀者層級不放寬「不可編造」（規範 0.3）：小辭典解釋的是詞，"
                "不是講者沒講的內容。",
            ])
        return lines
    return []


#: The errors no rule can see, as ``l2n review-prompt`` hands them to an
#: independent reviewer: (name, what to look for). Section 2.5 of the
#: guideline carries the same names with an example each.
REVIEW_ERRORS: Tuple[Tuple[str, str], ...] = (
    ("說過頭", "講者說「通常／可能／我覺得」，筆記寫成「一定／只有／絕不」"),
    ("夾帶因果", "講者只並列兩件事，筆記寫成「因為 A 所以 B」；推論沒標 [推論]"),
    ("替講者排順序", "講者沒排的判讀順序或流程，筆記排了，又沒標 [本筆記整理]"),
    ("單一示範當通則", "講者只示範一個病例或一個例子，筆記寫成一般特徵"),
    ("比較對象", "講者比的是 A 與 B，筆記寫成 A 與 C，或漏掉比較對象"),
    ("條件範圍", "講者的條件被放寬或收窄，或多了排他的「才」"),
    ("數量詞", "「約 10%」「少數」「大多數」被漏掉或改成全有全無"),
)

#: What `render --expand-prompt` adds for each ``[references]`` switch.
VERIFY_CITATIONS_STEP = (
    "講者或講義引用的文獻（作者、期刊、年份、DOI），先用 CrossRef 或 doi.org 查證；"
    "查得到才寫進 References，查不到就寫進 JSON 的 `unverified_terms`，本文不寫。"
    "`l2n check note` 不連網，不驗這一步。"
)
RECORD_HANDOUT_STEP = (
    "References 的 source 區塊加一行講義檔名與版本"
    "（例：`- handout: 講義檔名.pdf（版本或日期）`）；沒有講義就寫「無講義」。"
)

_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")


def find_guideline_doc(start: Optional[Path] = None) -> Optional[Path]:
    """Locate ``docs/note-writing-guideline.md``.

    A checkout has it at its repository path; an installed wheel has the copy
    the build mapped into ``lecture2notes/_bundled/docs/``, so since 0.2.1
    ``render --expand-prompt`` quotes the rules rather than only naming the
    file. Passing *start* keeps the old walk-up behaviour for a caller that
    wants to search from a particular directory. Callers still treat ``None``
    as "print the path, not the text".
    """
    if start is not None:
        here = Path(start)
        for parent in [here] + list(here.parents):
            candidate = parent / GUIDELINE_DOC
            if candidate.is_file():
                return candidate
        return None
    return resources.guideline_doc()


def section_text(markdown: str, heading_contains: str) -> str:
    """The body of the first heading whose text contains *heading_contains*.

    Stops at the next heading of the same or a shallower level, which is what
    "this part of the document" means in a Markdown file.
    """
    lines = markdown.replace("\r\n", "\n").split("\n")
    start: Optional[int] = None
    level = 0
    for position, line in enumerate(lines):
        match = _HEADING.match(line)
        if not match:
            continue
        if start is None:
            if heading_contains in match.group(1):
                start = position
                level = len(line) - len(line.lstrip("#"))
            continue
        if len(line) - len(line.lstrip("#")) <= level:
            return "\n".join(lines[start:position]).rstrip() + "\n"
    if start is None:
        return ""
    return "\n".join(lines[start:]).rstrip() + "\n"


def must_follow_text(doc: Optional[Path] = None) -> str:
    """The "LLM 必須遵守" part of the shipped document, or an empty string."""
    path = doc if doc is not None else find_guideline_doc()
    if path is None or not Path(path).is_file():
        return ""
    return section_text(
        Path(path).read_text(encoding="utf-8"), MUST_FOLLOW_HEADING
    )


def mandatory_headings() -> List[str]:
    return [heading for heading, _ in MANDATORY_SECTIONS]


def expand_prompt(
    json_path: Path,
    note_path: Optional[Path] = None,
    transcript_path: Optional[Path] = None,
    style: str = "concise",
    doc: Optional[Path] = None,
    reader: str = "self",
    verify_citations: bool = False,
    record_handout: bool = False,
) -> str:
    """The instruction bundle an agent needs to expand one skeleton.

    Printed by ``l2n render --expand-prompt``. It names the version of the rules,
    quotes the rules themselves when the document is on disk, lists the three
    files to read, and ends with the command that decides whether the expansion
    was acceptable. Everything an agent needs is in this one block of text, so
    the bundle can be pasted into any model without this package being installed
    on the other side.
    """
    json_path = Path(json_path)
    note = Path(note_path) if note_path else json_path.with_name(
        json_path.stem + ".v4.md"
    )
    transcript = Path(transcript_path) if transcript_path else json_path.with_name(
        json_path.stem + ".srt"
    )
    document = doc if doc is not None else find_guideline_doc()

    parts: List[str] = [
        VERSION_LINE,
        "",
        "# 擴寫任務",
        "",
        "把下面這份骨架筆記就地擴寫成完整筆記。章節順序與 frontmatter 不得更動。",
        "style: %s" % style,
        # The same value the note itself records after its frontmatter. Saying
        # so here stops an agent from "tidying up" the marker and silently
        # changing which rules its work is judged against.
        "（骨架 frontmatter 之後那行 `<!-- l2n:style=%s ... -->` 是這份筆記的風格來源，不要刪。）"
        % style,
        "",
        "## 要讀的檔案",
        "",
        "1. 骨架筆記（就地改寫這一份）: %s" % note,
        "2. 逐字稿: %s" % transcript,
        "3. 正式 JSON: %s" % json_path,
        "4. 官方講義（若有，請一併讀入；講義優先序高於逐字稿）",
        "",
        "## 撰寫規範",
        "",
        "規範文件: %s" % (document if document else GUIDELINE_DOC),
    ]
    rules = must_follow_text(document)
    if rules:
        parts.extend(["", rules.rstrip()])
    parts.extend(["", *reader_guidance(reader)])
    steps: List[str] = []
    if verify_citations:
        steps.append(VERIFY_CITATIONS_STEP)
    if record_handout:
        steps.append(RECORD_HANDOUT_STEP)
    if steps:
        parts.extend(["", "## References 的額外步驟（profile 開啟）", ""])
        parts.extend("- %s" % step for step in steps)
    parts.extend(["", *expansion_checklist(style)])
    parts.extend([
        "",
        "## 完成後必做",
        "",
        # --style is not optional here. `check note` defaults to the profile's
        # style, so an acceptance command without it silently runs concise
        # rules over a note written to the faithful contract, and R6 -- the one
        # rule faithful adds -- never fires. The value is the same one printed
        # under "style:" above, so the bundle cannot disagree with itself.
        "l2n check note %s --note %s --style %s" % (json_path, note, style),
        "",
        "check 回報任何 error 就回頭修，不要交出去。",
        "",
        "check 全過之後，交給沒參與撰寫的人或 agent 做獨立驗收（機器抓不到的錯誤）：",
        "",
        "l2n review-prompt %s --note %s" % (json_path, note),
    ])
    return "\n".join(parts) + "\n"


__all__ = [
    "ANSWER_CALLOUT",
    "BODY_INFERENCE_MARKS",
    "GLOSSARY_HEADING",
    "NOT_COVERED_NOTE",
    "READER_GUIDANCE",
    "READING_HEADING",
    "RECORD_HANDOUT_STEP",
    "REVIEW_ERRORS",
    "SPECULATION_WORDS",
    "VERIFY_CITATIONS_STEP",
    "reader_guidance",
    "DUPLICATE_SENTENCE_CHARS",
    "EVERGREEN_FIRST",
    "EXPANSION_CHECKLIST",
    "INFERENCE_PREFIX_HINT",
    "expansion_checklist",
    "FINDING_EXCERPT_CHARS",
    "GUIDELINE_DOC",
    "GUIDELINE_VERSION",
    "INFERENCE_MARK",
    "MACHINE_CHECKABLE_HEADING",
    "MANDATORY_SECTIONS",
    "MAX_QUESTIONS",
    "MIN_QUESTIONS",
    "MUST_FOLLOW_HEADING",
    "RULES",
    "TRANSCRIPT_RUN_CHARS",
    "VERSION_LINE",
    "expand_prompt",
    "find_guideline_doc",
    "mandatory_headings",
    "must_follow_text",
    "section_text",
]
