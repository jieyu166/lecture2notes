"""Bilingual subtitles: ``l2n bilingual export`` / ``status`` / ``merge``.

Translation is the model's work; everything around it is mechanical and lives
here. ``export`` cuts the subtitle into numbered batches a model can translate
one at a time, with a brief and a glossary; ``merge`` takes the translations
back and refuses anything that would move a cue:

* the subtitle must be byte-identical to the one that was exported (sha256),
* every cue must have exactly one translation, and no translation may name a
  cue that does not exist,
* the written files are parsed again and must carry the same cue count and the
  same timecodes as the source.

So a long lecture can be translated over several sittings, and whatever the
model does, ``<stem>.zh.srt`` and ``<stem>.bi.srt`` line up with the original
cue for cue.

The original text is never rewritten. When the translator decides the ASR
misheard a term, the translation uses the right term and the pair is recorded
in the document's ``corrections`` (source ``bilingual``); applying it to the
transcript stays the job of the existing correction table, which leaves a raw
backup and a sidecar behind (HARD RULE 2).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from lecture2notes.engines.base import Cue, cues_to_srt, parse_srt_text, read_subtitle_text
from lecture2notes.schema.io import read_json, write_json_atomic

#: The working folder beside the subtitle: ``<stem>.bilingual/``.
WORK_SUFFIX = ".bilingual"
MANIFEST = "manifest.json"
BRIEF = "brief.md"
#: Cues per batch. Small enough that a model keeps every id in view, large
#: enough that a three-hour lecture is a few dozen batches, not hundreds.
DEFAULT_BATCH_SIZE = 120
#: Cues of the previous batch shown as context, not to be translated.
CONTEXT_CUES = 3
#: The translation language. The file suffixes below are fixed to it.
TARGET_LANG = "zh"
#: Suffixes that mark a subtitle this module wrote, never a source.
OUTPUT_TAGS = (".bi", ".zh")
#: Correction-table sections, and what the brief calls each.
APPLIED_SECTIONS = ("deterministic", "radiology")
JUDGED_SECTIONS = ("context_sensitive",)
#: How many handout terms and near misses the glossary lists at most.
GLOSSARY_TERMS = 60
GLOSSARY_NEAR_MISSES = 40
#: How alike a transcript word and a handout term must be to be a near miss.
NEAR_MISS_RATIO = 0.84

TERM = re.compile(r"[A-Za-z][A-Za-z0-9+\-]*[A-Za-z0-9]")
STOPWORDS = frozenset("""
the and for with from that this these those are was were has have had not but
can may will its into over under than then there their about which what when
how why who all any one two use used using case cases also more most such you
your our they them she his her him been being very just like some here only
""".split())


class BilingualError(Exception):
    """A merge or export that would break the cue contract; nothing was written."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems[:5]))


@dataclass
class Target:
    """The subtitle being translated and, when there is one, its lecture document."""

    subtitle: Path
    stem: str
    document: Optional[Path] = None

    @property
    def work(self) -> Path:
        return self.subtitle.parent / (self.stem + WORK_SUFFIX)

    def output(self, tag: str) -> Path:
        return self.subtitle.parent / ("%s.%s.srt" % (self.stem, tag))


@dataclass
class MergeResult:
    target: Target
    lang: str
    cues: int
    outputs: List[Path] = field(default_factory=list)
    lines: int = 0
    corrections: List[Dict[str, Any]] = field(default_factory=list)
    unverified: List[str] = field(default_factory=list)


# -- locating the source ------------------------------------------------------

def _subtitle_stem(path: Path) -> str:
    stem = path.stem
    if stem.endswith(".raw"):
        raise BilingualError(["%s is the uncorrected backup; translate the corrected subtitle" % path.name])
    for tag in OUTPUT_TAGS:
        if stem.endswith(tag):
            raise BilingualError(["%s is a bilingual output, not a source subtitle" % path.name])
    return stem


def resolve(path: Path) -> Target:
    """A ``<stem>.json`` or a subtitle path, as the pair this module works on."""
    from lecture2notes.schema import lines as lines_mod

    path = Path(path)
    if path.suffix.lower() == ".json":
        data = read_json(path)
        subtitle = lines_mod.subtitle_for(path, data if isinstance(data, Mapping) else {})
        if subtitle is None:
            raise BilingualError(["no subtitle found for %s (pass the .srt instead)" % path.name])
        return Target(subtitle=subtitle, stem=_subtitle_stem(subtitle), document=path)
    stem = _subtitle_stem(path)
    document = path.parent / (stem + ".json")
    return Target(subtitle=path, stem=stem, document=document if document.is_file() else None)


def document_lang(target: Target) -> Optional[str]:
    """``source.subtitle.lang`` from the lecture document, when it says."""
    if target.document is None:
        return None
    data = read_json(target.document)
    source = data.get("source") if isinstance(data, Mapping) else None
    subtitle = source.get("subtitle") if isinstance(source, Mapping) else None
    lang = subtitle.get("lang") if isinstance(subtitle, Mapping) else None
    return str(lang) if lang else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_cues(path: Path) -> List[Cue]:
    return parse_srt_text(read_subtitle_text(Path(path)))


# -- glossary -------------------------------------------------------------------

def handout_text(handout: Optional[Path]) -> Tuple[str, str]:
    """``(text, note)``: the handout as plain text, and what happened reading it."""
    if handout is None or not Path(handout).is_file():
        return "", ""
    handout = Path(handout)
    if handout.suffix.lower() in (".txt", ".md"):
        return handout.read_text(encoding="utf-8", errors="replace"), "read %s" % handout.name
    if handout.suffix.lower() != ".pdf":
        return "", "%s: unsupported handout type, skipped" % handout.name
    tool = shutil.which("pdftotext")
    if tool is None:
        return "", "%s: pdftotext not found (poppler), terms not checked against the handout" % handout.name
    try:
        done = subprocess.run(
            [tool, "-layout", "-enc", "UTF-8", str(handout), "-"],
            capture_output=True, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", "%s: pdftotext failed (%s)" % (handout.name, exc)
    return done.stdout.decode("utf-8", "replace"), "read %s" % handout.name


def _terms(text: str) -> Counter:
    counts: Counter = Counter()
    for match in TERM.finditer(text):
        word = match.group(0)
        if word.lower() in STOPWORDS or (len(word) < 3 and not word.isupper()):
            continue
        counts[word] += 1
    return counts


def glossary(
    cues: Sequence[Cue], handout: str, table: Mapping[str, Any]
) -> Dict[str, List[str]]:
    """What the brief tells the translator about terms, section by section.

    ``keep``: handout terms the speaker also says -- written in English as-is.
    ``near``: transcript words that look like a misheard handout term.
    ``applied`` / ``judged``: correction-table pairs that occur in this
    transcript; the first were already applied, the second need judgement.
    """
    transcript = " ".join(cue.text for cue in cues)
    lowered = transcript.lower()
    out: Dict[str, List[str]] = {"keep": [], "near": [], "applied": [], "judged": []}

    if handout:
        handout_terms = _terms(handout)
        vocabulary = {word.lower() for word in handout_terms}
        spoken = _terms(transcript)
        out["keep"] = [
            word for word, _ in sorted(handout_terms.items(), key=lambda kv: (-kv[1], kv[0]))
            if word.lower() in lowered and (len(word) >= 4 or word.isupper())
        ][:GLOSSARY_TERMS]
        # Compared case-blind: "Emphysima" at the start of a cue is the same
        # near miss as "emphysima" in the middle of one.
        spelled = {w.lower(): w for w in sorted(handout_terms) if len(w) >= 5}
        candidates = sorted(spelled)
        seen = set()
        for word, _ in sorted(spoken.items(), key=lambda kv: (-kv[1], kv[0])):
            if len(word) < 5 or word.lower() in vocabulary or word.lower() in seen:
                continue
            match = difflib.get_close_matches(word.lower(), candidates, n=1, cutoff=NEAR_MISS_RATIO)
            if match:
                seen.add(word.lower())
                out["near"].append("%s -> %s" % (word, spelled[match[0]]))
            if len(out["near"]) >= GLOSSARY_NEAR_MISSES:
                break

    for sections, key in ((APPLIED_SECTIONS, "applied"), (JUDGED_SECTIONS, "judged")):
        for section in sections:
            pairs = table.get(section)
            if not isinstance(pairs, Mapping):
                continue
            for heard, correct in sorted(pairs.items()):
                if str(heard).lower() in lowered or str(correct).lower() in lowered:
                    out[key].append("%s -> %s" % (heard, correct))
    return out


# -- export -------------------------------------------------------------------

def _batch_name(number: int, suffix: str = ".json") -> str:
    return "batch-%03d%s" % (number, suffix)


def batch_payloads(cues: Sequence[Cue], size: int) -> List[Dict[str, Any]]:
    """The batch files: ids are 1-based cue positions, repeats point back."""
    size = max(int(size), 1)
    total = (len(cues) + size - 1) // size
    batches: List[Dict[str, Any]] = []
    for number in range(1, total + 1):
        first = (number - 1) * size
        entries: List[Dict[str, Any]] = []
        for position in range(first, min(first + size, len(cues))):
            cue_id = position + 1
            if position > 0 and cues[position].text == cues[position - 1].text:
                entries.append({"id": cue_id, "same_as": cue_id - 1})
            else:
                entries.append({"id": cue_id, "text": cues[position].text})
        context = [
            {"id": p + 1, "text": cues[p].text}
            for p in range(max(first - CONTEXT_CUES, 0), first)
        ]
        batches.append({
            "batch": number, "of": total,
            "context_before": context,
            "cues": entries,
        })
    return batches


def brief(target: Target, lang: str, cue_count: int, batches: int, size: int,
          terms: Mapping[str, Sequence[str]], handout_note: str) -> str:
    out = [
        "# 雙語字幕翻譯交辦書：%s" % target.stem,
        "",
        "- 來源：`%s`（原文 %s，%d 個 cue，分 %d 批，每批最多 %d 個）"
        % (target.subtitle.name, lang, cue_count, batches, size),
        "- 講義：%s" % (handout_note or "沒有同名講義"),
        "",
        "## 規則",
        "",
        "1. 每一批讀 `batch-NNN.json`，寫出 `batch-NNN.zh.json`。一個 `id` 一句台灣繁體中文譯文：",
        "   不合併、不拆分、不跳號。cue 數量與時間碼由程式保證不變，你只寫譯文。",
        "2. 專有名詞、縮寫、分級系統、藥名、序列名一律保留英文（例：Lung-RADS、T2WI、DWI）。",
        "3. 一句話被切在兩個 cue 時，各 cue 只譯自己那一段；可以依中文語序微調，但不要把字搬到別的 cue。",
        "4. `context_before` 只是上一批的結尾，供理解上下文，不要翻。標了 `same_as` 的 cue 與前一句相同，不用寫。",
        "5. 原文疑似 ASR 誤聽（對照下方術語表）：譯文用正確術語，並在 `corrections` 記一筆",
        "   `{\"heard\": 原文, \"correct\": 正確, \"cue\": id}`。**原文字幕不改**。",
        "6. 不確定的術語保留原文並加註「（可能為XXX）」，同時寫進 `unverified`。",
        "7. 不得補上字幕沒有的內容；聽不清的片段照原文的不完整程度翻。",
        "",
        "## 輸出格式（batch-NNN.zh.json）",
        "",
        "```json",
        "{",
        '  "zh": {"1": "譯文", "2": "譯文"},',
        '  "corrections": [{"heard": "Lung rats", "correct": "Lung-RADS", "cue": 2}],',
        '  "unverified": ["Fleischner（可能為 Fleischner Society）"]',
        "}",
        "```",
        "",
        "全部批次寫完後執行 `l2n bilingual merge %s`；中途可用 `l2n bilingual status %s` 看進度。"
        % (target.subtitle.name, target.subtitle.name),
        "",
        "## 術語表",
        "",
    ]
    sections = (
        ("keep", "講義與字幕都出現的術語（照寫英文）"),
        ("near", "字幕裡疑似誤聽的字（字幕 -> 講義寫法），逐處依上下文判斷"),
        ("applied", "錯字對照表已自動套用的組合（字幕已是右邊寫法）"),
        ("judged", "錯字對照表中需依語境判斷的組合（字幕 -> 可能的正確寫法）"),
    )
    empty = True
    for key, label in sections:
        values = list(terms.get(key) or [])
        if not values:
            continue
        empty = False
        out.append("### %s" % label)
        out.append("")
        out.append("、".join(values))
        out.append("")
    if empty:
        out.append("（沒有可用的術語資料）")
        out.append("")
    return "\n".join(out)


def export(
    target: Target,
    lang: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    handout: Optional[Path] = None,
    table: Optional[Mapping[str, Any]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Write ``<stem>.bilingual/``: manifest, brief and batch files.

    Translations already written (``batch-NNN.zh.json``) are never deleted. If
    the subtitle changed since they were written they no longer match any cue,
    so ``--force`` renames them to ``*.zh.stale.json`` instead.
    """
    if lang == TARGET_LANG:
        raise BilingualError(["the subtitle is already %s; nothing to translate" % TARGET_LANG])
    cues = read_cues(target.subtitle)
    if not cues:
        raise BilingualError(["%s has no cues" % target.subtitle.name])
    digest = _sha256(target.subtitle)
    work = target.work
    manifest_path = work / MANIFEST
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        same = manifest.get("sha256") == digest and manifest.get("batch_size") == batch_size
        if same and not force:
            return {"work": work, "skipped": True, "batches": manifest.get("batches"), "cues": len(cues)}
        translated = sorted(work.glob("batch-*.zh.json"))
        if translated and not force:
            raise BilingualError([
                "%s changed since export and %d batches are already translated; "
                "rerun with --force (old translations are kept as *.zh.stale.json)"
                % (target.subtitle.name, len(translated))
            ])
        for path in translated:
            path.replace(path.with_name(path.name.replace(".zh.json", ".zh.stale.json")))

    if handout is None:
        candidate = target.subtitle.with_name(target.stem + ".pdf")
        handout = candidate if candidate.is_file() else None
    text, note = handout_text(handout)
    terms = glossary(cues, text, table or {})
    batches = batch_payloads(cues, batch_size)

    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("batch-*.json"):
        if not old.name.endswith((".zh.json", ".zh.stale.json")):
            old.unlink()
    for payload in batches:
        write_json_atomic(work / _batch_name(payload["batch"]), payload)
    (work / BRIEF).write_text(
        brief(target, lang, len(cues), len(batches), batch_size, terms, note),
        encoding="utf-8", newline="\n",
    )
    write_json_atomic(manifest_path, {
        "stem": target.stem,
        "source": target.subtitle.name,
        "sha256": digest,
        "lang": lang,
        "target": TARGET_LANG,
        "cues": len(cues),
        "batch_size": batch_size,
        "batches": len(batches),
        "handout": note,
    })
    return {"work": work, "skipped": False, "batches": len(batches), "cues": len(cues), "handout": note}


def status(target: Target) -> Dict[str, Any]:
    """Which batches have a translation file."""
    manifest_path = target.work / MANIFEST
    if not manifest_path.is_file():
        raise BilingualError(["not exported yet: run l2n bilingual export %s" % target.subtitle.name])
    manifest = read_json(manifest_path)
    total = int(manifest.get("batches") or 0)
    done = [n for n in range(1, total + 1) if (target.work / _batch_name(n, ".zh.json")).is_file()]
    return {
        "batches": total,
        "done": done,
        "missing": [n for n in range(1, total + 1) if n not in done],
        "stale": manifest.get("sha256") != _sha256(target.subtitle),
    }


# -- merge ----------------------------------------------------------------------

def _clean(text: str) -> str:
    return " ".join(str(text).split())


def collect(target: Target) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]], List[str]]:
    """``(manifest, translations, corrections, unverified)``, or BilingualError."""
    info = status(target)
    manifest = read_json(target.work / MANIFEST)
    problems: List[str] = []
    if info["stale"]:
        problems.append(
            "%s changed since export; rerun l2n bilingual export --force" % target.subtitle.name
        )
    if info["missing"]:
        problems.append("batches not translated yet: %s" % ", ".join(
            _batch_name(n) for n in info["missing"][:20]
        ))
    if problems:
        raise BilingualError(problems)

    count = int(manifest["cues"])
    translations: List[Optional[str]] = [None] * count
    same_as: Dict[int, int] = {}
    corrections: List[Dict[str, Any]] = []
    unverified: List[str] = []
    for number in range(1, int(manifest["batches"]) + 1):
        batch = read_json(target.work / _batch_name(number))
        name = _batch_name(number, ".zh.json")
        try:
            answer = read_json(target.work / name)
        except (ValueError, UnicodeDecodeError) as exc:
            problems.append("%s is not valid JSON (%s)" % (name, exc))
            continue
        mapping = answer.get("zh") if isinstance(answer, Mapping) and "zh" in answer else answer
        if not isinstance(mapping, Mapping):
            problems.append('%s must hold {"zh": {"<id>": "譯文"}}' % name)
            continue
        expected = {int(entry["id"]) for entry in batch["cues"]}
        repeats = {int(e["id"]): int(e["same_as"]) for e in batch["cues"] if "same_as" in e}
        same_as.update(repeats)
        given: Dict[int, str] = {}
        for key, value in mapping.items():
            try:
                cue_id = int(key)
            except (TypeError, ValueError):
                problems.append("%s: %r is not a cue id" % (name, key))
                continue
            if cue_id not in expected:
                problems.append("%s: cue %d does not belong to this batch" % (name, cue_id))
                continue
            text = _clean(value) if isinstance(value, str) else ""
            if not text or "-->" in text:
                problems.append("%s: cue %d has an empty or malformed translation" % (name, cue_id))
                continue
            given[cue_id] = text
        missing = sorted(expected - set(given) - set(repeats))
        if missing:
            problems.append("%s: no translation for cue %s" % (
                name, ", ".join(str(i) for i in missing[:20]) + (" ..." if len(missing) > 20 else "")
            ))
        for cue_id, text in given.items():
            translations[cue_id - 1] = text
        if isinstance(answer, Mapping):
            for item in answer.get("corrections") or []:
                if isinstance(item, Mapping) and item.get("heard") and item.get("correct"):
                    corrections.append({
                        "heard": str(item["heard"]), "correct": str(item["correct"]),
                        "source": "bilingual", "cue": item.get("cue"),
                    })
            for item in answer.get("unverified") or []:
                if isinstance(item, str) and item.strip() and item not in unverified:
                    unverified.append(item.strip())
    for cue_id in sorted(same_as):
        if translations[cue_id - 1] is None:
            translations[cue_id - 1] = translations[same_as[cue_id] - 1]
    gaps = [i + 1 for i, text in enumerate(translations) if text is None]
    if gaps and not problems:
        problems.append("no translation for cue %s" % ", ".join(str(i) for i in gaps[:20]))
    if problems:
        raise BilingualError(problems)
    return manifest, [str(t) for t in translations], corrections, unverified


def _timings(cues: Sequence[Cue]) -> List[Tuple[float, float]]:
    return [(round(c.start, 3), round(c.end, 3)) for c in cues]


def _update_document(
    target: Target, cues: Sequence[Cue], translations: Sequence[str],
    corrections: Sequence[Mapping[str, Any]], unverified: Sequence[str],
) -> int:
    """Write ``lines[].zh`` (and the recorded corrections) into the document."""
    from lecture2notes.schema import lines as lines_mod
    from lecture2notes.schema.model import SCHEMA_VERSION, is_legacy

    if target.document is None:
        return 0
    data = read_json(target.document)
    if not isinstance(data, dict) or is_legacy(data):
        return 0
    data["lines"] = lines_mod.lines_from_cues(cues, translations)
    data["schema_version"] = SCHEMA_VERSION
    recorded = data.get("corrections") if isinstance(data.get("corrections"), list) else []
    known = {(c.get("heard"), c.get("correct")) for c in recorded if isinstance(c, Mapping)}
    for item in corrections:
        key = (item["heard"], item["correct"])
        if key not in known:
            known.add(key)
            recorded.append({"heard": item["heard"], "correct": item["correct"], "source": "bilingual"})
    data["corrections"] = recorded
    terms = data.get("unverified_terms") if isinstance(data.get("unverified_terms"), list) else []
    data["unverified_terms"] = terms + [t for t in unverified if t not in terms]
    write_json_atomic(target.document, data)
    return len(data["lines"])


def merge(target: Target) -> MergeResult:
    """Write ``<stem>.<lang>.srt``, ``<stem>.zh.srt``, ``<stem>.bi.srt`` and ``lines[].zh``."""
    manifest, translations, corrections, unverified = collect(target)
    cues = read_cues(target.subtitle)
    if len(cues) != len(translations):
        raise BilingualError(["%d cues but %d translations" % (len(cues), len(translations))])
    lang = str(manifest.get("lang") or "src")
    rendered = {
        lang: [Cue(c.start, c.end, c.text) for c in cues],
        TARGET_LANG: [Cue(c.start, c.end, zh) for c, zh in zip(cues, translations)],
        "bi": [Cue(c.start, c.end, "%s\n%s" % (zh, c.text)) for c, zh in zip(cues, translations)],
    }
    # Parse what is about to be written before writing any of it: a translation
    # that smuggles in something the parser reads as structure fails here.
    for tag, items in rendered.items():
        again = parse_srt_text(cues_to_srt(items))
        if _timings(again) != _timings(cues):
            raise BilingualError(["%s output would not keep the source cues and timecodes" % tag])
    result = MergeResult(target, lang, len(cues), corrections=list(corrections), unverified=list(unverified))
    for tag, items in rendered.items():
        path = target.output(tag)
        path.write_text(cues_to_srt(items), encoding="utf-8", newline="\n")
        result.outputs.append(path)
    result.lines = _update_document(target, cues, translations, corrections, unverified)
    return result


__all__ = [
    "BilingualError",
    "DEFAULT_BATCH_SIZE",
    "MergeResult",
    "Target",
    "WORK_SUFFIX",
    "batch_payloads",
    "brief",
    "collect",
    "document_lang",
    "export",
    "glossary",
    "handout_text",
    "merge",
    "resolve",
    "status",
]
