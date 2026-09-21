# Attribution

This document records the provenance of every module under
`src/lecture2notes/`, relative to two upstream projects this project's name
and problem domain overlap with:

- [`drpwchen/lecture-to-notes`](https://github.com/drpwchen/lecture-to-notes)
  (MIT) — audited at HEAD commit `79053a30814330842f3fd195333a4d79b698ef88`,
  cloned read-only on 2026-09-21.
- [`drpwchen/asr-benchmark`](https://github.com/drpwchen/asr-benchmark) (MIT)
  — cited by name in one file's own docstring as the empirical source of two
  ASR default parameters (see "ASR default parameters" below).

## Upstream license text

`drpwchen/lecture-to-notes`, `LICENSE`, verbatim:

```
MIT License

Copyright (c) 2026 drpwchen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Statement

A file-by-file review of every module under `src/lecture2notes/` against
`drpwchen/lecture-to-notes` at `79053a3` found **no file that is a direct
modification of upstream source code** — no shared function bodies, control
flow, or file structure. Seven modules were judged **inspired**: they address
the same pipeline stage as a specific upstream file, and in two cases
(`frames/curator.py`, `engines/faster_whisper.py` / `engines/breeze_ct2.py`)
share a well-known, generic technique (perceptual-hash near-duplicate
detection; the faster-whisper wrapper shape) with an upstream file, but were
independently implemented with different function names, different data
flow, and materially different scope. The remaining modules are either
**ported from this same project's own prior work** in the `rad-workflow`
repository (a private monorepo this project was extracted from — not a
third-party upstream) or **original**, written directly for this package.

繁體中文摘要：逐檔比對 `src/lecture2notes/` 全部模組與上游
`drpwchen/lecture-to-notes`（`79053a3`），**未發現任何檔案直接修改自上游程式碼**
（無相同的函式內容、控制流程或檔案結構）。7 個模組被歸類為「inspired」：處理
與某個上游檔案相同的流程階段，其中 2 組（`frames/curator.py`；
`engines/faster_whisper.py` / `engines/breeze_ct2.py`）與上游共用一種常見的
通用技術（感知雜湊去重；faster-whisper 包裝外形），但函式名稱、資料流與範圍
都是獨立設計、明顯不同。其餘模組若非「移植自本專案自己在 `rad-workflow`
（本專案抽取出來源的私有 monorepo，非第三方上游）的既有成果」，就是「原創」
——直接為本套件撰寫。

## Module table

Relation values:

- **inspired** — addresses the same pipeline stage as a named upstream file
  and/or shares a generic technique with it; independently implemented.
  Upstream path required.
- **ported-from-rad-workflow** — carried over from this project's own prior
  script in the `rad-workflow` repository (not a third-party upstream); no
  `drpwchen/lecture-to-notes` path applies.
- **original** — written directly for this package, no prior script.

| Module | Relation | Upstream path (`drpwchen/lecture-to-notes`) | Note |
|---|---|---|---|
| `__init__.py` | original | — | Package marker. |
| `__main__.py` | original | — | `python -m lecture2notes` entry point. |
| `_deps.py` | original | — | Optional-dependency presence checks (`require_rapidocr`, etc.). |
| `_out.py` | original | — | Shared `Progress`/console-output helper. |
| `exit_codes.py` | original | — | Exit-code constants for the CLI contract. |
| `acceptance/__init__.py` | original | — | Package marker. |
| `acceptance/audit.py` | inspired | `scripts/audit_note.py` | Both produce a structured pass/fail audit record; upstream audits frontmatter/wikilinks/citations for a vault note, this audits a canonical lecture JSON's derivatives (viewer/chapter-file/note) against each other. No shared function names. |
| `acceptance/check.py` | inspired | `scripts/audit_note.py` | Shares only the FAIL/WARN severity-tier philosophy (a generic QA pattern, named explicitly in upstream's own docstring); checks (JSON structure, frame existence, time monotonicity) and function names are unrelated. |
| `acceptance/rebuild.py` | ported-from-rad-workflow | — | From `rad-workflow` `.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/rebuild_course.py`; no upstream analog beyond the generic idea of a course rebuild. |
| `cli/__init__.py` | original | — | Package marker. |
| `cli/main.py` | original | — | This package's own subcommand dispatch; not present upstream in this form. |
| `engines/__init__.py` | original | — | Package marker. |
| `engines/audio.py` | original | — | Single ffmpeg audio-extraction and duration contract for all engines; the rad-workflow scripts each shelled out to their own ffmpeg line. No upstream analog. |
| `engines/base.py` | ported-from-rad-workflow | — | From `rad-workflow` `skills/whisper-srt-zh/scripts/transcribe.py` (timestamp formatting, cue-writing loop) and `skills/lecture-to-notes/scripts/build_lecture_viewer.py` (`parse_srt`); defines this package's own `Engine`/`Cue` interface, not present upstream. |
| `engines/breeze_ct2.py` | inspired | `scripts/transcribe_video.py` | Shares the faster-whisper wrapper shape and CLI-flag surface inherent to the faster-whisper API itself (`beam_size`, `compute_type`, `vad_filter`, …), not copied prose; adds the CTranslate2 Breeze-ASR-25 model-directory checks, which upstream does not have. |
| `engines/calibrate.py` | original | — | Session-written subtitle-offset measurement and drift-fit code (`offset3.py`/`vtt_fix.py` in `rad-workflow`, never itself a port of an upstream or rad-workflow *package* file — see `docs/group2.md`). No upstream analog. |
| `engines/corrections.py` | ported-from-rad-workflow | — | From `rad-workflow` `skills/whisper-srt-zh/scripts/correct_srt.py`. Upstream's equivalent (`flag_asr_suspects.py`) is deliberately flag-only by design (never rewrites); this module does the opposite on purpose (deterministic find/replace with an audit sidecar) — an independently-designed third approach, not derived from either of upstream's two rejected auto-rewrite attempts. |
| `engines/convert.py` | original | — | `l2n convert-model`: one-time Hugging Face to CTranslate2 conversion with a disk estimate and an overwrite refusal. No upstream or rad-workflow prior script. |
| `engines/faster_whisper.py` | inspired | `scripts/transcribe_video.py` | Same reasoning as `breeze_ct2.py`: shared flag surface is inherent to the faster-whisper API, not copied; `--engine` selects something different in each project (cloud-vs-GPU upstream vs. local-engine-choice here). |
| `engines/hallucination.py` | original | — | Repeated-cue loop detection for the Whisper-family failure mode that looks like a working transcript. No upstream analog. |
| `engines/pipeline.py` | original | — | The transcribe stage glue (audio, engine, SRT, corrections sidecar); this package's own stage contract, not present upstream. |
| `engines/qwen3_asr.py` | original | — | New engine registration for Qwen3-ASR; no upstream or rad-workflow prior script. |
| `engines/registry.py` | original | — | This package's own `--engine` lookup and `--allow-cloud` gate; no prior script. |
| `engines/whisper_cpp.py` | ported-from-rad-workflow | — | From `rad-workflow` `skills/whisper-srt-zh/scripts/transcribe.py` (the `--engine whisper.cpp` subprocess branch). |
| `frames/__init__.py` | original | — | Package marker. |
| `frames/curator.py` | inspired | `scripts/extract_slides.py` | Both use perceptual-hash + Hamming-distance near-duplicate detection — a well-known, generic computer-vision technique, not upstream-specific code — embedded in an otherwise unrelated staging/promotion/manifest workflow with no upstream counterpart. |
| `frames/interval.py` | original | — | Session-written fixed-interval frame sampling with a grayscale mean-abs-diff dedup (`pacs_frames.py` in `rad-workflow`, itself never a port of an upstream file). No upstream analog. |
| `frames/manifest.py` | ported-from-rad-workflow | — | From `rad-workflow` `skills/lecture-to-notes/scripts/slide_frames.py` (JSON-merge portion); the shared manifest record format itself is this package's own addition. |
| `frames/ocr.py` | inspired | `scripts/quick_ocr.py` | Same stage (OCR every extracted frame) and same third-party OCR engine choice (`rapidocr-onnxruntime`), but different purpose and output: this module feeds a source-priority note-writing rule (handout > slide > transcript > OCR) with Traditional-Chinese normalization (OpenCC) that upstream does not have; upstream feeds semantic dedup and a VLM skip-gate. No shared function names. |
| `frames/scene.py` | ported-from-rad-workflow | — | From `rad-workflow` `skills/lecture-to-notes/scripts/slide_frames.py` (PySceneDetect / ffmpeg scene-filter detection). Upstream's `extract_slides.py` explicitly rejects scene detection for lecture recordings and uses interval-sampling instead — the opposite algorithm, not a shared design. |
| `notes/__init__.py` | original | — | Package marker. |
| `notes/render.py` | ported-from-rad-workflow | — | From `rad-workflow` `.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/render_v4_note.py`. Upstream's `render_embeds.py` expands embed placeholders inside an existing note in a different format; different input/output convention, no shared functions. |
| `notes/rewrite.py` | ported-from-rad-workflow | — | From `rad-workflow` `skills/lecture-to-notes/scripts/rewrite_evidence.py` (only the sensitive-data-pattern and text-normalization helpers were kept). No upstream file addresses claims/citations/evidence packets at all. |
| `notes/rules.py` | ported-from-rad-workflow | — | From `rad-workflow` `.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/lecture_content_rules.py`. No upstream equivalent (Traditional/Simplified Chinese content rules are specific to this project's language target). |
| `outputs/__init__.py` | original | — | Package marker. |
| `outputs/batch.py` | ported-from-rad-workflow | — | From `rad-workflow` `skills/lecture-to-notes/scripts/batch_course.py`. Upstream's closest analog (`route_inputs.py`) only classifies a folder and prints a plan; it does not execute the pipeline as this module does. |
| `outputs/hub.py` | inspired | `scripts/export_web.py` | Upstream embeds a "hub" (multi-lecture home page) concept inside a much larger export pipeline (`pick_hub`, `type_badge_html`); this module ships it standalone with a distinct feature (cross-lecture full-text search deep-linking to `?t=<seconds>`). No shared function names. |
| `outputs/images.py` | ported-from-rad-workflow | — | From `rad-workflow` `skills/lecture-to-notes/scripts/collect_note_images.py`. No upstream equivalent. |
| `outputs/pbf.py` | ported-from-rad-workflow | — | From `rad-workflow` `skills/lecture-to-notes/scripts/json_to_pbf.py` (PotPlayer `.pbf` bookmark export). No upstream script touches PotPlayer or `.pbf` at all. |
| `outputs/publish.py` | ported-from-rad-workflow | — | From `rad-workflow` `.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/publish_transaction.py`. Upstream's `finalize_to_vault.py` is a plain copy-with-refresh-check; this module is a full transactional publish system (manifest, atomic replace-with-rollback, recovery) with no shared design. |
| `outputs/viewer.py` | ported-from-rad-workflow | — | From `rad-workflow` `skills/lecture-to-notes/scripts/build_lecture_viewer.py`. Benchmarked in its own upstream (rad-workflow) docstring against a different in-workspace tool, not against `drpwchen/lecture-to-notes`'s `export_web.py` / `build_single_talk_web.py`; no shared function names with either. |
| `profiles/__init__.py` | original | — | Package marker; ships the `generic`/`radiology` corrections-table profiles (written from scratch for this project; not derived from any third-party correction table). |
| `schema/__init__.py` | original | — | Package marker. |
| `schema/builder.py` | original | — | Session-written lecture-document builder (`mkseg3.py` in `rad-workflow`, never itself a port of an upstream file). No upstream analog. |
| `schema/condense.py` | original | — | Session-written per-minute transcript condensing (`condense_agent.py` in `rad-workflow`, never itself a port of an upstream file). No upstream analog. |
| `schema/io.py` | original | — | The single atomic, BOM-free, LF-only canonical JSON writer/reader. No upstream analog. |
| `schema/migrate.py` | original | — | `l2n migrate`: upgrades a 1.x lecture document to canonical schema v2. No upstream analog. |
| `schema/model.py` | ported-from-rad-workflow | — | From `rad-workflow` `.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/lecture_model.py`. No upstream canonical-schema module or time-signature validator exists in `drpwchen/lecture-to-notes`. |

Counts: 7 inspired, 15 ported-from-rad-workflow, 26 original — 48 modules total.

## ASR default parameters

`engines/faster_whisper.py` and `engines/breeze_ct2.py` default
`vad_filter=False` and `condition_on_previous_text=False`. These two default
values are credited by name in the module docstrings (mirroring the
attribution already present in the rad-workflow source,
`skills/whisper-srt-zh/scripts/transcribe.py`) to
[`drpwchen/asr-benchmark`](https://github.com/drpwchen/asr-benchmark) — a
**different** repository from `drpwchen/lecture-to-notes` — as the empirical
source: both defaults were measured, not guessed. No code was copied from
`asr-benchmark`; only the two parameter *values* are attributed.

`drpwchen/asr-benchmark` is also MIT-licensed. Its `LICENSE` copyright line:

```
Copyright (c) 2026 Po-Wei Chen (陳柏威)
```

Full text is reproduced in `NOTICE`.
