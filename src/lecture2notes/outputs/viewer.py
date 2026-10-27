"""The synced lecture viewer: cues, blocks, their time bindings and the page.

Ported from rad-workflow
skills/lecture-to-notes/scripts/build_lecture_viewer.py (``frame_seconds``,
``read_text``, ``parse_srt``, ``find_sibling``, ``build_blocks``, and the page
template, which is rewritten here against schema v2).

Synchronisation is exact for the transcript layer, because every cue has a real
timecode. In schema v2 a bullet carries its own ``t``; when it is null the time
is interpolated evenly inside the segment and flagged. The flag is not
decoration: a reader who does not know which times are guesses will trust the
wrong ones, so an estimated time is printed with a leading tilde and a real one
never is.

The page is one self-contained HTML file. No stylesheet, script or font is
fetched from a network, because the whole point of the output is that a lecture
folder copied onto a laptop still works with the network off.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from lecture2notes.engines.base import Cue, parse_srt, parse_srt_text, read_subtitle_text
from lecture2notes.frames.manifest import frame_seconds
from lecture2notes.outputs.plan import PlannedFile, plan_for

VIDEO_EXT = (".mp4", ".m4v", ".webm", ".mov", ".mkv")
SUBTITLE_EXT = (".srt", ".vtt")
#: Block kinds, in the order the viewer layers them.
KINDS = ("slide", "summary", "quote", "transcript")
#: Suffix of the generated page.
VIEWER_SUFFIX = ".viewer.html"


def find_sibling(base: Path, stem: str, extensions: Sequence[str]) -> Optional[Path]:
    """Find a companion file: exact stem first, then a stem prefix.

    ``.raw.srt`` is excluded. It is the uncorrected original that the correction
    step leaves behind, and it sorts before ``.srt``, so a glob that does not
    exclude it silently picks the version with the known errors in it.
    """
    base = Path(base)

    def usable(path: Path) -> bool:
        return path.exists() and ".raw." not in path.name.lower()

    for ext in extensions:
        exact = base / ("%s%s" % (stem, ext))
        if usable(exact):
            return exact
        for candidate in sorted(base.glob("%s*%s" % (stem, ext))):
            if usable(candidate):
                return candidate
    hits = [
        path for path in sorted(base.iterdir())
        if path.suffix.lower() in extensions and usable(path)
    ]
    return hits[0] if len(hits) == 1 else None


def clock(seconds: float) -> str:
    """``mm:ss``, or ``h:mm:ss`` once the lecture passes an hour."""
    total = max(int(float(seconds or 0)), 0)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%02d:%02d" % (minutes, secs)


def bullet_text(bullet: Any) -> str:
    """The text of a v2 bullet object or of a legacy bare string."""
    if isinstance(bullet, Mapping):
        return str(bullet.get("text") or "")
    return str(bullet)


def segment_view(segment: Mapping[str, Any]) -> Dict[str, Any]:
    """One segment as the viewer needs it."""
    start = float(segment.get("start_sec") or 0)
    end = float(segment.get("end_sec") or start)
    return {
        "id": "s%s" % segment.get("index"),
        "index": segment.get("index"),
        "title": segment.get("title") or "",
        "start": start,
        "end": end,
        "summary": segment.get("summary_zh") or "",
        "frames": segment.get("frames") or (
            [segment["frame"]] if segment.get("frame") else []
        ),
        # Schema 2.1 fields, passed through as they are; the page hides what a
        # document does not have, so a 2.0 or migrated 1.x file still renders.
        "question": segment.get("question") if isinstance(segment.get("question"), Mapping) else None,
        "triage": segment.get("triage") if isinstance(segment.get("triage"), Mapping) else None,
    }


def _real_time(value: Any) -> Optional[float]:
    """A usable timecode, or None. ``True`` is not a timecode."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def build_blocks(
    data: Mapping[str, Any],
    cues: Sequence[Cue],
    zh: Optional[Sequence[str]] = None,
    frame_times: Optional[Mapping[str, float]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return ``(segments, blocks)``: every clickable line with its time.

    Slide OCR text becomes its own block timed from the frame manifest when
    there is one (``frame_times``), else from the frame filename, so clicking a
    line of slide text jumps to the moment that slide appeared. Quotes carry a
    real ``t`` of their own and are never marked estimated. ``zh`` runs
    parallel to ``cues`` and gives each transcript block its translation.
    """
    frame_times = frame_times or {}
    segments: List[Dict[str, Any]] = []
    blocks: List[Dict[str, Any]] = []
    for segment in data.get("segments", []):
        if not isinstance(segment, Mapping):
            continue
        view = segment_view(segment)
        segments.append(view)
        sid, start, end = view["id"], view["start"], view["end"]

        for entry in segment.get("frame_ocr") or []:
            if not isinstance(entry, Mapping):
                continue
            text = (entry.get("text") or "").strip()
            if not text:
                continue
            name = str(entry.get("frame", ""))
            at = float(frame_times.get(name, frame_seconds(name, start)))
            blocks.append({
                "id": "%sf%d" % (sid, len(blocks)),
                "seg": sid, "kind": "slide",
                "start": at, "end": at + 1,
                "text": text, "est": False,
                "frame": str(entry.get("frame", "")),
            })

        bullets = [
            bullet for bullet in (segment.get("bullets_zh") or [])
            if bullet_text(bullet).strip()
        ]
        span = max(end - start, 0.001)
        slots = max(len(bullets), 1)
        for position, bullet in enumerate(bullets):
            text = bullet_text(bullet)
            explicit = _real_time(bullet.get("t")) if isinstance(bullet, Mapping) else None
            kind = (bullet.get("kind") if isinstance(bullet, Mapping) else None) or "synthesis"
            if explicit is not None:
                block_start = explicit
                block_end = min(block_start + span / slots, end)
                estimated = False
            else:
                block_start = round(start + span * position / slots, 2)
                block_end = round(start + span * (position + 1) / slots, 2)
                estimated = True
            blocks.append({
                "id": "%sb%d" % (sid, position),
                "seg": sid, "kind": "summary",
                "start": block_start, "end": block_end,
                "text": text, "est": estimated, "bullet_kind": str(kind),
            })

        for position, quote in enumerate(segment.get("quotes_zh") or []):
            raw = quote.get("text") if isinstance(quote, Mapping) else quote
            text = str(raw or "").strip()
            if not text:
                continue
            at = _real_time(quote.get("t")) if isinstance(quote, Mapping) else None
            quote_start = start if at is None else at
            blocks.append({
                "id": "%sq%d" % (sid, position),
                "seg": sid, "kind": "quote",
                "start": quote_start,
                "end": max(min(quote_start + 8.0, end), quote_start + 0.5),
                "text": text, "est": at is None,
            })

    for position, cue in enumerate(cues):
        owner = next(
            (s for s in segments if s["start"] <= cue.start < s["end"]), None
        )
        if owner is None and segments:
            owner = min(segments, key=lambda s: abs(s["start"] - cue.start))
        block = {
            "id": "t%d" % position,
            "seg": owner["id"] if owner else "",
            "kind": "transcript",
            "start": cue.start, "end": cue.end,
            "text": cue.text, "est": False,
        }
        if zh is not None and position < len(zh) and zh[position]:
            block["zh"] = zh[position]
        blocks.append(block)
    return segments, blocks


def estimated_block_count(blocks: Sequence[Mapping[str, Any]]) -> int:
    """How many blocks carry an interpolated rather than a real time."""
    return sum(1 for block in blocks if block.get("est"))


def canonical_spine(data: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The chapter spine the audit reads back out of the page.

    Deliberately the same four fields as ``acceptance.audit.canonical_snapshot``:
    the audit compares the two, so a viewer that quietly reordered or retitled a
    chapter is caught instead of being trusted.
    """
    spine: List[Dict[str, Any]] = []
    for position, segment in enumerate(data.get("segments") or []):
        if not isinstance(segment, Mapping):
            continue
        spine.append({
            "index": segment.get("index", position + 1),
            "start_sec": float(segment.get("start_sec") or 0),
            "end_sec": float(segment.get("end_sec") or 0),
            "title": segment.get("title"),
        })
    return spine


# --------------------------------------------------------------------------
# page template
# --------------------------------------------------------------------------
# One layout serves both screens. On a desktop the video and its chapter list
# sit on the left and the reading panes on the right; below 760px the video
# stays pinned at the top and exactly one pane shows at a time, chosen from a
# tab bar at the bottom. Every pane is in the page on both screens and the
# mode class on <body> decides which are visible, so a resize never has to
# rebuild anything.
CSS = """
:root{--bg:#0f1115;--panel:#171a21;--line:#272c37;--fg:#e6e9ef;--dim:#9aa3b2;--accent:#4da3ff;--hit:#f5c451;--new:#3fb950;--known:#6e7681;--review:#d29922}
*{box-sizing:border-box}
html{height:100%;overflow:hidden}
body{margin:0;font:16px/1.65 "Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;background:var(--bg);color:var(--fg);height:100vh;height:100dvh;display:flex;flex-direction:column;overflow:hidden}
header{flex:0 0 auto;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;padding:.5rem .8rem;background:var(--panel);border-bottom:1px solid var(--line);z-index:40}
header h1{font-size:1rem;margin:0 .4rem 0 0;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:30vw}
button,select{background:#222833;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:.25rem .6rem;cursor:pointer;font-size:.85rem;font-family:inherit}
button:hover{border-color:var(--accent)}
button.active{background:var(--accent);color:#08101c;border-color:var(--accent);font-weight:600}
button.ghost{background:none}
[hidden]{display:none!important}
.spacer{flex:1}
.search{position:relative}
.search input{background:#0d1016;border:1px solid var(--line);color:var(--fg);border-radius:6px;padding:.3rem .6rem;width:15rem;font-size:.85rem;font-family:inherit}
#results{position:absolute;top:110%;right:0;width:min(34rem,calc(100vw - 2rem));max-height:60vh;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px;display:none;z-index:50}
#results.open{display:block}
.sr{display:grid;grid-template-columns:4.5rem minmax(0,1fr) 4rem;gap:.4rem;width:100%;text-align:left;background:none;border:0;border-bottom:1px solid var(--line);padding:.4rem .6rem;border-radius:0}
.sr:hover{background:#1e2530}
.sr .k{color:var(--dim);font-size:.72rem}
.sr .t{font-size:.82rem;overflow-wrap:anywhere}
.sr .ts{color:var(--accent);font-size:.75rem;text-align:right}
.count{padding:.3rem .6rem;color:var(--dim);font-size:.75rem}
#resume{color:var(--hit);border-color:#5a4a1c}
main{flex:1 1 auto;min-height:0;overflow:hidden;display:grid;grid-template-columns:var(--vcol,44%) 6px minmax(0,1fr)}
#vpane{padding:.6rem;min-height:0;min-width:0;overflow:hidden;display:flex;flex-direction:column}
#vhead{flex:0 0 auto}
#vwrap{position:relative;background:#000;border-radius:8px;overflow:hidden;touch-action:manipulation}
video{display:block;width:100%;max-height:62vh;background:#000}
#vwrap:fullscreen video{max-height:100vh;height:100%}
#subs{position:absolute;left:4%;right:4%;bottom:calc(6% + 46px);text-align:center;pointer-events:none;z-index:5}
#subs div{display:inline-block;max-width:100%;background:#000b;padding:.05em .45em;border-radius:4px;line-height:1.4;text-shadow:0 1px 2px #000}
#subs .s-zh{font-size:clamp(.95rem,2.2vw,1.35rem);color:#fff}
#subs .s-or{font-size:clamp(.8rem,1.8vw,1.1rem);color:#d8dde6;margin-top:.15em}
#subs .s-zh+br+.s-or{margin-top:.2em}
#flash{position:absolute;top:40%;left:50%;transform:translate(-50%,-50%);background:#000a;color:#fff;padding:.4rem .9rem;border-radius:20px;font-size:1rem;pointer-events:none;z-index:6}
#qcard{position:absolute;left:4%;right:4%;top:6%;max-height:80%;overflow:auto;background:#141a24f2;border:1px solid var(--accent);border-radius:10px;padding:.7rem .9rem;z-index:7;font-size:.95rem}
#qcard .qh{color:var(--accent);font-size:.8rem;margin-bottom:.2rem}
#qcard .qa{margin:.5rem 0;color:var(--hit)}
#qcard .row{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.5rem}
#timeline{position:relative;height:22px;margin-top:.45rem;cursor:pointer;touch-action:none}
#tl-track{position:absolute;left:0;right:0;top:7px;height:8px;border-radius:4px;background:#232a36;overflow:hidden}
.tl-seg{position:absolute;top:0;bottom:0;background:#34557d;border-left:2px solid var(--bg)}
.tl-seg.known{background:#2a2f38;opacity:.55}
.tl-seg.review{background:#5a4a1c}
.tl-seg.must{background:#2f6b45}
.tl-seg.on{filter:brightness(1.45)}
#tl-ab{position:absolute;top:0;bottom:0;background:#f5c45155;display:none}
#tl-pos{position:absolute;top:2px;width:3px;height:18px;margin-left:-1px;background:var(--hit);border-radius:2px;left:0}
#tl-tip{position:absolute;bottom:24px;transform:translateX(-50%);white-space:nowrap;max-width:min(26rem,90vw);overflow:hidden;text-overflow:ellipsis;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:.15rem .5rem;font-size:.75rem;pointer-events:none;display:none;z-index:8}
#ctrl{display:flex;gap:.35rem;flex-wrap:wrap;margin-top:.45rem;align-items:center}
#ctrl button{font-size:.8rem}
#abtag{font-size:.75rem;color:var(--hit)}
#now{font-size:.8rem;color:var(--dim);margin-top:.35rem;min-height:1.4em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#segnav{flex:1 1 auto;min-height:0;overflow:auto;margin-top:.6rem;display:flex;flex-direction:column;gap:.3rem}
.segcard{text-align:left;padding:.4rem .6rem;font-size:.85rem;line-height:1.4;width:100%}
.segcard.on{border-color:var(--accent);background:#1b2635}
.segcard b{color:var(--accent);margin-right:.4rem;font-weight:600}
.segcard .sub{color:var(--dim);font-size:.75rem}
.segcard.known{opacity:.6}
.nov{display:inline-block;font-size:.7rem;border-radius:9px;padding:0 .45rem;margin-left:.35rem;vertical-align:1px;border:1px solid}
.nov-新知{color:var(--new);border-color:var(--new)}
.nov-已知{color:var(--known);border-color:var(--known)}
.nov-複習{color:var(--review);border-color:var(--review)}
#vsplit,#hsplit{background:var(--line);cursor:col-resize}
#hsplit{cursor:row-resize;height:6px;flex:0 0 6px}
#notes{display:flex;flex-direction:column;min-width:0;min-height:0;overflow:hidden}
.pane{overflow:auto;min-height:0;padding:.6rem .9rem;display:none;flex:1 1 auto;overflow-wrap:anywhere}
.pane h2{font-size:.8rem;color:var(--dim);margin:.2rem 0 .6rem;font-weight:600;letter-spacing:.05em}
#hsplit{display:none}
body.mode-split #spane{display:block;flex:0 0 var(--srow,45%)}
body.mode-split #hsplit{display:block}
body.mode-split #tpane,body.mode-tr #tpane,body.mode-sum #spane,body.mode-quiz #qpane,body.mode-triage #xpane,body.mode-chap #cpane{display:block}
.seg{margin-bottom:1.1rem}
.seg>h3{font-size:.95rem;margin:.2rem 0 .3rem}
.seg>h3 .no{color:var(--accent)}
.sum{color:var(--dim);font-size:.85rem;margin:0 0 .4rem}
.blk{display:block;width:100%;text-align:left;background:none;border:0;border-left:3px solid transparent;border-radius:0;padding:.18rem .5rem;color:var(--fg);font-size:.9rem;line-height:1.6}
.blk:hover{background:#1c222c;border-left-color:var(--dim)}
.blk.on{background:#233246;border-left-color:var(--hit)}
.blk .ts{color:var(--accent);font-size:.75rem;margin-right:.45rem;font-variant-numeric:tabular-nums}
.blk .zh{display:block;color:#b9c3d3;font-size:.85em;padding-left:.1rem}
.blk.est .ts{color:var(--dim)}
.blk.quote{color:#cfd6e2;border-left-color:#4b5668}
.blk.quote .qm{color:var(--dim)}
#tpane .blk{font-size:.86rem;padding:.1rem .5rem}
.thumbs{display:flex;gap:.3rem;flex-wrap:wrap;margin:.3rem 0 .5rem}
.thumbs img{height:72px;max-width:100%;border-radius:4px;border:1px solid var(--line);cursor:zoom-in}
.ocr{margin:.3rem 0 .2rem}
.ocr>summary{cursor:pointer;color:var(--dim);font-size:.78rem;padding:.15rem .5rem}
.blk.slide{white-space:pre-wrap;font-size:.8rem;color:var(--dim);border-left-color:#3a4557}
.blk.slide:hover{color:var(--fg)}
.card{border:1px solid var(--line);border-radius:8px;padding:.6rem .75rem;margin-bottom:.6rem;background:#141820}
.card h3{font-size:.9rem;margin:0 0 .35rem}
.card .no{color:var(--accent)}
.card .a{color:var(--hit);margin:.4rem 0 0}
.card .row{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.45rem}
.verdict{font-size:1.15rem;font-weight:700;margin:.1rem 0 .3rem}
.stat{color:var(--dim);font-size:.85rem}
.chips{display:flex;gap:.35rem;flex-wrap:wrap;margin:.45rem 0}
.empty{color:var(--dim);font-size:.85rem}
.note{flex:0 0 auto;font-size:.72rem;color:var(--dim);padding:.25rem .9rem;background:var(--panel);border-top:1px solid var(--line);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#tabs{display:none}
.mobile-only{display:none}
mark{background:var(--hit);color:#111}
#toast{position:fixed;left:50%;bottom:4.5rem;transform:translateX(-50%);background:#e6e9ef;color:#0f1115;padding:.45rem 1rem;border-radius:20px;font-size:.9rem;z-index:90;opacity:0;transition:opacity .2s;pointer-events:none;max-width:90vw}
#toast.show{opacity:1}
#modal{position:fixed;inset:0;background:#000a;z-index:95;display:flex;align-items:center;justify-content:center;padding:1rem}
#modal .box{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:1rem;width:min(40rem,100%);max-height:90vh;overflow:auto}
#modal h2{font-size:1rem;margin:0 0 .6rem}
#modal textarea{width:100%;height:50vh;background:#0d1016;color:var(--fg);border:1px solid var(--line);border-radius:6px;font:inherit;font-size:.85rem;padding:.5rem}
#modal table{border-collapse:collapse;width:100%;font-size:.85rem}
#modal td{padding:.2rem .4rem;border-bottom:1px solid var(--line);vertical-align:top}
#modal kbd{background:#222833;border:1px solid var(--line);border-radius:4px;padding:0 .35rem;font-family:inherit}
#modal label{display:flex;gap:.5rem;align-items:center;margin:.4rem 0}
body.float #vpane{position:fixed;right:1rem;bottom:1rem;width:var(--fw,26rem);z-index:60;background:var(--panel);border:1px solid var(--line);border-radius:10px;box-shadow:0 8px 30px #0009;resize:both;overflow:auto;max-height:80vh}
body.float #segnav{display:none}
body.float main{grid-template-columns:0 0 1fr}
@media(max-width:760px){
  header{padding:.35rem .6rem;gap:.35rem}
  header h1{max-width:none;flex:1 1 0;min-width:0;font-size:.95rem}
  .desktop-only,#segnav,#vsplit,.note{display:none!important}
  .mobile-only{display:inline-block}
  .search{order:9;flex:1 1 100%}
  .search input{width:100%;min-height:40px;font-size:16px}
  main{display:flex;flex-direction:column}
  #vpane{flex:0 0 auto;padding:0;overflow:visible}
  #vwrap{border-radius:0}
  video{max-height:34vh}
  #timeline{height:30px;margin:.2rem .6rem 0}
  #tl-track{top:11px}
  #tl-pos{top:6px}
  #ctrl{margin:.15rem .6rem 0;gap:.3rem;flex-wrap:nowrap;overflow-x:auto}
  #ctrl button{min-height:44px;padding:.2rem .6rem;flex:0 0 auto}
  #now{display:none}
  #notes{flex:1 1 auto}
  .pane{padding:.5rem .7rem}
  button{min-height:44px}
  .blk{min-height:44px;padding:.45rem .5rem}
  #tpane .blk{padding:.4rem .5rem}
  .segcard{min-height:48px;padding:.5rem .7rem}
  #tabs{display:flex;flex:0 0 auto;background:var(--panel);border-top:1px solid var(--line);padding-bottom:env(safe-area-inset-bottom)}
  #tabs button{flex:1 1 0;min-width:0;border:0;border-radius:0;background:none;min-height:50px;font-size:.82rem;padding:0}
  #tabs button.active{color:var(--accent);background:#1b2635;font-weight:600}
  #qcard{top:3%;font-size:.9rem}
  body.float #vpane{position:static;width:auto}
}
"""

JS = r"""
var DATA = JSON.parse(document.getElementById('viewer-data').textContent);
var S = DATA.segments;
var B = DATA.blocks.slice().sort(function(a,b){return a.start-b.start;});
var T = B.filter(function(b){return b.kind==='transcript';});
var HAS_ZH = T.some(function(b){return !!b.zh;});
var LINKS = DATA.links || {};
var TRI = DATA.triage_overall || null;
var MUST = (TRI && TRI.must_watch) || [];
var KIND_LABEL = {summary:'摘要', transcript:'逐字', slide:'投影片', quote:'原話'};
var auto=true, suspend=0, progScroll=false, activeSeg='';
var $=function(s,r){return (r||document).querySelector(s);};
var $$=function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s));};
var fmt=function(t){t=Math.max(0,Math.floor(t||0));var h=Math.floor(t/3600),m=Math.floor(t%3600/60),s=t%60;
  return (h?h+':'+String(m).padStart(2,'0'):String(m).padStart(2,'0'))+':'+String(s).padStart(2,'0');};
var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});};
var V=$('#player');
var mobile=function(){return window.matchMedia('(max-width: 760px)').matches;};

/* Per-viewer preferences and the resume position. Storage can be missing or
   throw (private windows, blocked site data); the page works without it. */
var store={get:function(k,d){try{var v=localStorage.getItem(k);return v===null?d:JSON.parse(v);}catch(e){return d;}},
  set:function(k,v){try{localStorage.setItem(k,JSON.stringify(v));}catch(e){}},
  del:function(k){try{localStorage.removeItem(k);}catch(e){}}};
var POS_KEY='l2n:pos:'+location.pathname;

/* Links are relative in the page and made absolute here, so the text the
   page copies works wherever the folder is served from. */
var abs=function(rel){if(!rel)return '';try{return new URL(rel,location.href).href;}catch(e){return rel;}};
var duration=function(){var d=V.duration;if(isFinite(d)&&d>0)return d;return DATA.duration||(S.length?S[S.length-1].end:0)||1;};
function segAt(t){for(var i=S.length-1;i>=0;i--){if(t>=S[i].start)return i;}return 0;}
function lineAt(t,strict){var lo=0,hi=T.length-1,hit=-1;
  while(lo<=hi){var mid=(lo+hi)>>1;if(T[mid].start<=t){hit=mid;lo=mid+1;}else hi=mid-1;}
  if(hit<0)return null; var b=T[hit]; if(strict&&t>b.end+0.3)return null; return b;}
function novelty(s){return s&&s.triage&&s.triage.novelty||'';}

var toastTimer;
function toast(msg){var el=$('#toast');el.textContent=msg;el.classList.add('show');clearTimeout(toastTimer);
  toastTimer=setTimeout(function(){el.classList.remove('show');},1800);}
var flashTimer;
function flash(msg){var el=$('#flash');el.textContent=msg;el.hidden=false;clearTimeout(flashTimer);
  flashTimer=setTimeout(function(){el.hidden=true;},700);}

/* preload="metadata" has not finished when the page opens, and a currentTime
   set before that is dropped on the floor, so a seek waits for
   loadedmetadata instead of assuming it took. */
function seek(t,play){
  t=Math.max(0,t);
  var go=function(){ try{V.currentTime=t;}catch(e){}
    if(play!==false){ var p=V.play(); if(p&&p.catch){p.catch(function(){});} } };
  if(V.readyState===0){ V.addEventListener('loadedmetadata',go,{once:true}); try{V.load();}catch(e){} }
  else { go(); }
  sync(t,true);
}
function skipBy(d){seek((V.currentTime||0)+d,!V.paused);}

function sync(t,force){
  paintTimeline(t); renderSubs(t);
  var si=segAt(t), seg=S[si];
  if(seg&&seg.id!==activeSeg){ activeSeg=seg.id;
    $$('.segcard').forEach(function(e){e.classList.toggle('on',e.dataset.seg===activeSeg);});
    $$('.tl-seg').forEach(function(e){e.classList.toggle('on',e.dataset.seg===activeSeg);});
    $$('.segcard.on').forEach(function(c){ if(force||follow()) scrollInto(c); });
    if(!force) maybeQuiz(seg);
  }
  var hits=B.filter(function(b){return b.start-0.15<=t && t<b.end+0.15;});
  if(!hits.length) return;
  $$('.blk.on').forEach(function(e){e.classList.remove('on');});
  var lead=hits.filter(function(b){return b.kind==='transcript';})[0]||hits[0];
  $('#now').textContent=fmt(t)+'  '+(seg?seg.index+'. '+seg.title:'');
  hits.forEach(function(h){
    var el=document.getElementById('b-'+h.id); if(!el) return;
    el.classList.add('on');
    if(force||follow()) scrollInto(el);
  });
}
function follow(){ return auto && Date.now()>suspend && !V.paused; }
function scrollInto(el){
  var pane=el.closest('.pane, #segnav'); if(!pane||pane.offsetParent===null) return;
  var target=pane.scrollTop+(el.getBoundingClientRect().top-pane.getBoundingClientRect().top)-pane.clientHeight*0.42;
  progScroll=true; pane.scrollTo({top:Math.max(0,target),behavior:'auto'});
  setTimeout(function(){progScroll=false;},80);
}
/* Scrolling by hand suspends the follow for five seconds: without it, looking
   back one paragraph yanks the reader forward again. */
$$('.pane, #segnav').forEach(function(p){
  ['wheel','touchmove'].forEach(function(ev){p.addEventListener(ev,function(){
    if(!progScroll&&auto&&!V.paused){ suspend=Date.now()+5000; paint(); }},{passive:true});});});
function paint(){ var b=$('#autoscroll'); if(!b) return;
  b.textContent='自動捲動：'+(!auto?'關':(Date.now()<suspend?'暫停':'開'));
  b.classList.toggle('active',auto&&Date.now()>=suspend); }
setInterval(paint,1000);

/* -- chapter timeline ---------------------------------------------------- */
function buildTimeline(){
  var track=$('#tl-track'); var D=duration();
  $$('.tl-seg',track).forEach(function(e){e.remove();});
  S.forEach(function(s){
    var el=document.createElement('div'); var n=novelty(s);
    el.className='tl-seg'+(n==='已知'?' known':n==='複習'?' review':'')+(MUST.indexOf(s.index)>=0?' must':'');
    el.dataset.seg=s.id;
    el.style.left=(s.start/D*100)+'%'; el.style.width=(Math.max(s.end-s.start,0)/D*100)+'%';
    track.insertBefore(el,$('#tl-ab'));
  });
}
function paintTimeline(t){ var D=duration();
  $('#tl-pos').style.left=Math.min(100,(t/D*100))+'%';
  var ab=$('#tl-ab');
  if(abA!=null){ ab.style.display='block'; ab.style.left=(abA/D*100)+'%';
    ab.style.width=(((abB!=null?abB:t)-abA)/D*100)+'%'; } else ab.style.display='none'; }
(function(){ var tl=$('#timeline'), tip=$('#tl-tip');
  var at=function(e){var r=tl.getBoundingClientRect();var x=(e.touches?e.touches[0].clientX:e.clientX)-r.left;
    return {x:x,t:Math.min(Math.max(x/r.width,0),1)*duration()};};
  var show=function(e){var p=at(e);var s=S[segAt(p.t)];var n=novelty(s);
    tip.textContent=fmt(p.t)+'  '+(s?s.index+'. '+s.title+(n?'（'+n+'）':''):'');
    tip.style.left=Math.min(Math.max(p.x,60),tl.clientWidth-60)+'px'; tip.style.display='block';};
  tl.addEventListener('mousemove',show); tl.addEventListener('mouseleave',function(){tip.style.display='none';});
  tl.addEventListener('touchstart',show,{passive:true});
  tl.addEventListener('touchend',function(){setTimeout(function(){tip.style.display='none';},900);});
  tl.addEventListener('click',function(e){seek(at(e).t,!V.paused);});
})();

/* -- subtitles on the video ------------------------------------------------- */
var SUB_LABEL={bi:'雙語',zh:'中文',orig:'原文',off:'關'};
var subMode=store.get('l2n:pref:sub',HAS_ZH?'bi':'orig');
if(!HAS_ZH&&(subMode==='bi'||subMode==='zh')) subMode='orig';
var subsHtml='';
function renderSubs(t){ var box=$('#subs');
  var b=subMode==='off'?null:lineAt(t,true);
  var html='';
  if(b){ if(subMode==='bi'){ if(b.zh) html+='<div class="s-zh">'+esc(b.zh)+'</div><br>'; html+='<div class="s-or">'+esc(b.text)+'</div>'; }
    else if(subMode==='zh'){ html='<div class="s-zh">'+esc(b.zh||b.text)+'</div>'; }
    else html='<div class="s-or">'+esc(b.text)+'</div>'; }
  if(html!==subsHtml){box.innerHTML=html;subsHtml=html;} box.hidden=!html; }
function setSub(m){ subMode=m; store.set('l2n:pref:sub',m); $('#subbtn').textContent='字幕：'+SUB_LABEL[m]; renderSubs(V.currentTime||0); }
function cycleSub(){ var order=HAS_ZH?['bi','zh','orig','off']:['orig','off'];
  var next=order[(order.indexOf(subMode)+1)%order.length]; setSub(next);
  toast('字幕：'+SUB_LABEL[next]+(HAS_ZH?'':'（這支沒有中文字幕）')); }

/* -- speed ------------------------------------------------------------------ */
var RATES=[0.5,0.75,1,1.25,1.5,1.75,2,2.5,3];
function setRate(r){ V.playbackRate=r; V.defaultPlaybackRate=r; store.set('l2n:pref:rate',r); $('#speed').textContent=r+'x'; }
function stepRate(d){ var i=RATES.indexOf(V.playbackRate); if(i<0) i=2; i=Math.min(RATES.length-1,Math.max(0,i+d)); setRate(RATES[i]); toast('速度 '+RATES[i]+'x'); }

/* -- A-B repeat ------------------------------------------------------------- */
var abA=null, abB=null;
function abTag(){ $('#abtag').textContent=abA==null?'':(abB==null?'A '+fmt(abA)+'，待按 B':'重播 '+fmt(abA)+'–'+fmt(abB)); paintTimeline(V.currentTime||0); }
function pressA(){ if(abA!=null&&abB!=null){abA=abB=null;toast('已清除區段重播');} else {abA=V.currentTime||0;abB=null;toast('A 點 '+fmt(abA)+'，再按 B 設終點');} abTag(); }
function pressB(){ if(abB!=null){abA=abB=null;toast('已清除區段重播');} else if(abA==null){toast('先按 A 設起點');} else {abB=Math.max(V.currentTime||0,abA+0.5);toast('區段重播 '+fmt(abA)+'–'+fmt(abB));seek(abA,true);} abTag(); }

/* -- question mode ---------------------------------------------------------- */
var quizOn=store.get('l2n:pref:quiz',true), quizShown={};
/* A question belongs to the start of its segment: pressing play half way
   through one does not stop to ask it, and a segment being skipped by the
   must-watch mode never asks. */
var QUIZ_WINDOW=15;
function maybeQuiz(seg){ if(!quizOn||!seg||!seg.question||quizShown[seg.id]||V.paused) return;
  if((V.currentTime||0)-seg.start>QUIZ_WINDOW) return;
  if(skipOn&&MUST.indexOf(seg.index)<0) return;
  quizShown[seg.id]=1; showQ(seg); }
function showQ(seg){ var q=seg.question, card=$('#qcard'); V.pause();
  card.innerHTML='<div class="qh">第 '+esc(seg.index)+' 段題目　'+esc(seg.title)+'</div><div>'+esc(q.q)+'</div>'
    +'<div class="qa" hidden>答：'+esc(q.a)+'</div><div class="row">'
    +'<button data-act="ans">看答案（跳到 '+fmt(q.answer_sec)+'）</button><button data-act="cont">先看影片</button>'
    +'<button data-act="quizoff" class="ghost">關閉題目模式</button></div>';
  card.dataset.seg=seg.id; card.hidden=false; }
$('#qcard').addEventListener('click',function(e){ var act=e.target.closest('[data-act]'); if(!act) return;
  var card=$('#qcard'); var seg=S.filter(function(s){return s.id===card.dataset.seg;})[0];
  if(act.dataset.act==='ans'&&seg){ $('.qa',card).hidden=false; act.remove(); $('[data-act=cont]',card).textContent='關閉'; seek(seg.question.answer_sec,true); }
  else if(act.dataset.act==='cont'){ card.hidden=true; if(V.paused){var p=V.play();if(p&&p.catch)p.catch(function(){});} }
  else if(act.dataset.act==='quizoff'){ setQuiz(false); card.hidden=true; }
});
function setQuiz(on){ quizOn=on; store.set('l2n:pref:quiz',on); var b=$('#quizbtn'); b.textContent='題目模式：'+(on?'開':'關'); b.classList.toggle('active',on); }

/* -- watch only the must-watch segments ------------------------------------- */
var skipOn=false;
function setSkip(on){ skipOn=on&&MUST.length>0; $$('[data-act=skip]').forEach(function(b){b.classList.toggle('active',skipOn);b.textContent=skipOn?'只播必看段：開':'只播必看段：關';});
  if(skipOn) toast('只播第 '+MUST.join('、')+' 段'); }
function enforceSkip(t){ if(!skipOn||V.paused) return; var seg=S[segAt(t)]; if(!seg||MUST.indexOf(seg.index)>=0) return;
  var next=S.filter(function(s){return MUST.indexOf(s.index)>=0&&s.start>t;})[0];
  if(next) seek(next.start,true); else { V.pause(); setSkip(false); toast('必看段已播完'); } }

/* -- resume ----------------------------------------------------------------- */
var lastSave=0;
function savePos(force){ var t=V.currentTime||0, now=Date.now(); if(!force&&now-lastSave<3000) return; lastSave=now;
  if(t>5&&t<duration()-10) store.set(POS_KEY,Math.floor(t)); else if(t>=duration()-10) store.del(POS_KEY); }
window.addEventListener('pagehide',function(){savePos(true);});

/* -- copy: ask-AI text and the S capture ------------------------------------ */
var CTX_OPTIONS=[30,60,120,'seg'];
var ctxLen=store.get('l2n:pref:ctx',60); if(CTX_OPTIONS.indexOf(ctxLen)<0) ctxLen=60;
function lineText(b){ return '['+fmt(b.start)+'] '+b.text+(b.zh?' ／ '+b.zh:''); }
function askText(){
  var t=V.currentTime||0, seg=S[segAt(t)]||{index:'',title:'',start:0,end:duration()}, from, to;
  if(ctxLen==='seg'){ from=seg.start; to=seg.end; } else { from=Math.max(0,t-ctxLen); to=t+Math.round(ctxLen/2); }
  var lines=T.filter(function(b){return b.start>=from&&b.start<to;}).map(lineText);
  var out=['影片：'+abs(LINKS.video)+'#t='+Math.floor(t)];
  if(LINKS.subtitle) out.push('字幕：'+abs(LINKS.subtitle)+(LINKS.bilingual?'（雙語：'+abs(LINKS.bilingual)+'）':''));
  else if(LINKS.bilingual) out.push('字幕：'+abs(LINKS.bilingual));
  if(LINKS.json) out.push('分段 JSON：'+abs(LINKS.json));
  out.push('目前位置：'+fmt(t)+'，第 '+seg.index+' 段「'+seg.title+'」');
  out.push('上下文（'+fmt(from)+'–'+fmt(to)+' 的逐字稿'+(HAS_ZH?'，原文 ／ 中文':'')+'）：');
  out=out.concat(lines.length?lines:['（這段沒有逐字稿）']);
  out.push(''); out.push('我的問題：');
  return out.join('\n');
}
function copyText(text,done){
  var fallback=function(){
    var ta=document.createElement('textarea'); ta.value=text; ta.setAttribute('readonly','');
    ta.style.position='fixed'; ta.style.opacity='0'; document.body.appendChild(ta); ta.select();
    var ok=false; try{ok=document.execCommand('copy');}catch(e){} ta.remove();
    if(ok) toast(done); else showCopyModal(text); };
  if(navigator.clipboard&&window.isSecureContext){ navigator.clipboard.writeText(text).then(function(){toast(done);},fallback); }
  else fallback();
}
function askAI(){ copyText(askText(),'已複製，貼到任一 AI 後接著打你的問題'); }
function capture(){ var t=V.currentTime||0, b=lineAt(t,false);
  copyText('[▶ '+fmt(t)+']('+abs(LINKS.video)+'#t='+Math.floor(t)+')'+(b?' '+b.text+(b.zh?' ／ '+b.zh:''):''),'已擷取 '+fmt(t)); }

/* -- modal: help, settings, copy fallback ----------------------------------- */
function openModal(html){ var m=$('#modal'); $('.box',m).innerHTML=html; m.hidden=false; }
function closeModal(){ $('#modal').hidden=true; }
$('#modal').addEventListener('click',function(e){ if(e.target.id==='modal'||e.target.closest('[data-act=close]')) closeModal(); });
function showCopyModal(text){
  openModal('<h2>複製失敗，請手動全選複製</h2><textarea id="copybox" readonly></textarea><div style="margin-top:.6rem"><button data-act="close">關閉</button></div>');
  var ta=$('#copybox'); ta.value=text; ta.focus(); ta.select(); }
var KEYS=[['空白','播放／暫停'],['左右方向鍵','倒退／前進 5 秒'],['J / K','上一段／下一段'],['A / B','設定區段重播起點／終點（再按清除）'],
  ['[ / ]','減速／加速'],['C','字幕：雙語、中文、原文、關 循環切換'],['Q','問 AI：複製目前位置與上下文'],['S','擷取「▶ 時間 + 當句字幕」'],
  ['/','搜尋'],['?','這份說明'],['Esc','關閉視窗']];
function showHelp(){ openModal('<h2>快捷鍵</h2><table>'+KEYS.map(function(k){return '<tr><td><kbd>'+esc(k[0])+'</kbd></td><td>'+esc(k[1])+'</td></tr>';}).join('')
  +'</table><p class="stat">手機：雙擊影片左右半邊倒退／前進 10 秒。</p>'+settingsHtml()+'<div style="margin-top:.6rem"><button data-act="close">關閉</button></div>'); bindSettings(); }
function settingsHtml(){ return '<h2 style="margin-top:1rem">設定</h2><label>問 AI 的上下文 <select id="ctxsel">'
  +CTX_OPTIONS.map(function(v){return '<option value="'+v+'"'+(v===ctxLen?' selected':'')+'>'+(v==='seg'?'整段':'前 '+v+' 秒、後 '+Math.round(v/2)+' 秒')+'</option>';}).join('')
  +'</select></label><label><input type="checkbox" id="quizchk"'+(quizOn?' checked':'')+'> 題目模式（進入每段先出題）</label>'; }
function bindSettings(){ var sel=$('#ctxsel'); if(sel) sel.addEventListener('change',function(){ ctxLen=sel.value==='seg'?'seg':Number(sel.value); store.set('l2n:pref:ctx',ctxLen); toast('上下文已更新'); });
  var chk=$('#quizchk'); if(chk) chk.addEventListener('change',function(){setQuiz(chk.checked);}); }
function showSettings(){ openModal(settingsHtml()+'<div style="margin-top:.6rem"><button data-act="close">關閉</button></div>'); bindSettings(); }

/* -- panes rendered from the data ------------------------------------------- */
function renderQuiz(){ var pane=$('#qpane'); var qs=S.filter(function(s){return s.question;});
  var html='<h2>題目（每段一題，答案出自講者原話）</h2>';
  if(!qs.length&&!(DATA.questions_zh||[]).length){ pane.innerHTML=html+'<p class="empty">這份 JSON 還沒有題目（schema 2.1 的 segments[].question）。</p>'; return; }
  html+=qs.map(function(s){ var q=s.question; return '<div class="card"><h3><span class="no">'+esc(s.index)+'</span> '+esc(s.title)+'</h3><div>'+esc(q.q)+'</div>'
    +'<p class="a" hidden>答：'+esc(q.a)+'</p><div class="row"><button data-act="reveal">看答案</button>'
    +'<button data-t="'+q.answer_sec+'" data-act="jump">▶ 講解處 '+fmt(q.answer_sec)+'</button><button data-t="'+s.start+'" data-act="jump" class="ghost">從本段開頭看</button></div></div>'; }).join('');
  var cross=DATA.questions_zh||[];
  if(cross.length) html+='<h2 style="margin-top:1rem">跨段題目</h2>'+cross.map(function(q){ return '<div class="card"><div>'+esc(q.text)+'</div><div class="row">'
    +(q.segments||[]).map(function(i){var s=S[i-1];return s?'<button data-t="'+s.start+'" data-act="jump" class="ghost">第 '+i+' 段</button>':'';}).join('')+'</div></div>'; }).join('');
  pane.innerHTML=html; }
function renderTriage(){ var pane=$('#xpane'); var rows=S.filter(function(s){return s.triage;});
  var html='<h2>分流（看之前先決定看哪幾段）</h2>';
  if(!TRI&&!rows.length){ pane.innerHTML=html+'<p class="empty">這份 JSON 還沒有分流（schema 2.1 的 triage_overall 與 segments[].triage）。</p>'; return; }
  var mins=function(v){return v<10?String(Math.round(v*10)/10):String(Math.round(v));};
  if(TRI){ var total=duration()/60, est=Number(TRI.est_minutes)||0;
    html+='<div class="card"><div class="verdict">'+esc(TRI.verdict)+'</div><div class="stat">必看約 '+mins(est)+' 分鐘／全片 '+mins(total)+' 分鐘'
      +(total-est>0.5?'，省下約 '+mins(total-est)+' 分鐘':'')+'</div><p>'+esc(TRI.reason)+'</p>'
      +(MUST.length?'<div class="chips">'+MUST.map(function(i){var s=S[i-1];return s?'<button data-t="'+s.start+'" data-act="jump">第 '+i+' 段 '+esc(s.title)+'</button>':'';}).join('')+'</div>'
        +'<button data-act="skip">只播必看段：關</button>':'')+'</div>'; }
  html+=rows.map(function(s){ var x=s.triage; return '<div class="card"><h3><span class="no">'+esc(s.index)+'</span> '+esc(s.title)
    +'<span class="nov nov-'+esc(x.novelty)+'">'+esc(x.novelty)+'</span></h3><div class="stat">'+fmt(s.start)+'–'+fmt(s.end)+'</div><div>'+esc(x.reason)+'</div>'
    +((x.related_notes||[]).length?'<div class="stat">既有筆記：'+x.related_notes.map(esc).join('、')+'</div>':'')
    +'<div class="row"><button data-t="'+s.start+'" data-act="jump" class="ghost">▶ 看這段</button></div></div>'; }).join('');
  pane.innerHTML=html; }

/* -- modes: desktop reading layouts and mobile tabs ------------------------- */
var MODES=['sum','split','tr','quiz','triage','chap'];
function setMode(m){ if(!mobile()&&m==='chap') m='split'; if(mobile()&&m==='split') m='tr';
  MODES.forEach(function(x){document.body.classList.toggle('mode-'+x,x===m);});
  $$('[data-mode]').forEach(function(b){b.classList.toggle('active',b.dataset.mode===m);});
  store.set('l2n:pref:mode:'+(mobile()?'m':'d'),m);
  if(m==='tr'||m==='chap'||m==='split') sync(V.currentTime||0,true); }
window.matchMedia('(max-width: 760px)').addEventListener('change',function(){ setMode(store.get('l2n:pref:mode:'+(mobile()?'m':'d'),mobile()?'chap':'split')); });

/* -- events ----------------------------------------------------------------- */
var raf=0;
function loop(){ var t=V.currentTime||0; renderSubs(t); paintTimeline(t); raf=V.paused?0:requestAnimationFrame(loop); }
V.addEventListener('play',function(){ if(!raf) raf=requestAnimationFrame(loop); enforceSkip(V.currentTime||0); maybeQuiz(S[segAt(V.currentTime||0)]); });
V.addEventListener('timeupdate',function(){ var t=V.currentTime||0;
  if(abB!=null&&t>=abB){ seek(abA,true); return; }
  enforceSkip(t); if(!V.paused) sync(t,false); savePos(false); });
V.addEventListener('loadedmetadata',function(){ buildTimeline(); setRate(store.get('l2n:pref:rate',1)); });
document.addEventListener('click',function(e){
  var blk=e.target.closest('.blk'); if(blk){ seek(Number(blk.dataset.t)); return; }
  var card=e.target.closest('.segcard'); if(card){ seek(Number(card.dataset.t)); return; }
  var m=e.target.closest('[data-mode]'); if(m){ setMode(m.dataset.mode); return; }
  var act=e.target.closest('[data-act]');
  if(act&&!act.closest('#qcard')&&!act.closest('#modal')){ var a=act.dataset.act;
    if(a==='jump'){ seek(Number(act.dataset.t)); if(mobile()) {} return; }
    if(a==='reveal'){ var p=act.closest('.card').querySelector('.a'); if(p) p.hidden=false; act.remove(); return; }
    if(a==='skip'){ setSkip(!skipOn); if(skipOn) enforceSkip(V.currentTime||0); return; } }
  var img=e.target.closest('.thumbs img'); if(img){ window.open(img.src,'_blank'); return; }
  if(!e.target.closest('.search')) $('#results').classList.remove('open');
});
$('#autoscroll').addEventListener('click',function(){auto=!auto;suspend=0;paint();});
$('#float').addEventListener('click',function(e){document.body.classList.toggle('float');e.target.classList.toggle('active');});
var fs=1; var zoom=function(d){fs=Math.min(1.7,Math.max(0.85,fs+d));document.documentElement.style.fontSize=(16*fs)+'px';};
$('#zin').addEventListener('click',function(){zoom(0.1);}); $('#zout').addEventListener('click',function(){zoom(-0.1);});
$('#subbtn').addEventListener('click',cycleSub);
$('#speed').addEventListener('click',function(){ var i=RATES.indexOf(V.playbackRate); var order=[1,1.25,1.5,1.75,2,0.75];
  var j=order.indexOf(V.playbackRate); setRate(order[(j+1)%order.length]); });
$('#quizbtn').addEventListener('click',function(){setQuiz(!quizOn); toast('題目模式：'+(quizOn?'開':'關'));});
$$('.askai').forEach(function(b){b.addEventListener('click',askAI);});
$('#capbtn').addEventListener('click',capture);
$('#helpbtn').addEventListener('click',showHelp);
$$('.setbtn').forEach(function(b){b.addEventListener('click',showSettings);});
$('#fsbtn').addEventListener('click',function(){ var w=$('#vwrap');
  if(document.fullscreenElement){document.exitFullscreen();return;}
  if(w.requestFullscreen) w.requestFullscreen().catch(function(){}); else if(V.webkitEnterFullscreen) V.webkitEnterFullscreen(); });
$('#resume').addEventListener('click',function(){ seek(Number($('#resume').dataset.t),true); $('#resume').hidden=true; });

/* Double-tap on the left or right half of the video: back or forward 10 s.
   The bottom strip is left alone because the native controls live there. */
(function(){ var last=0,lastX=0,w=$('#vwrap');
  w.addEventListener('touchend',function(e){ var r=w.getBoundingClientRect(), touch=e.changedTouches[0];
    if(touch.clientY>r.bottom-48||e.target.closest('#qcard')) return;
    var now=Date.now();
    if(now-last<320&&Math.abs(touch.clientX-lastX)<90){ e.preventDefault(); var left=touch.clientX<r.left+r.width/2;
      skipBy(left?-10:10); flash(left?'« 10 秒':'10 秒 »'); last=0; }
    else { last=now; lastX=touch.clientX; } },{passive:false}); })();

/* One search box over every layer: summary, quotes, slide text, transcript
   and its translation. */
function search(qs){
  var box=$('#results');
  var terms=(qs||'').trim().toLowerCase().split(/\s+/).filter(Boolean);
  if(!terms.length){ box.classList.remove('open'); box.innerHTML=''; return; }
  var hay=function(b){return (b.text+' '+(b.zh||'')).toLowerCase();};
  var hits=B.filter(function(b){var h=hay(b);return terms.every(function(t){return h.indexOf(t)>=0;});}).slice(0,80);
  if(!hits.length){ box.innerHTML='<div class="count">無符合結果</div>'; box.classList.add('open'); return; }
  box.innerHTML='<div class="count">'+hits.length+' 筆</div>'+hits.map(function(b){
    var full=b.text+(b.zh?' ／ '+b.zh:''); var pos=Math.max(0,full.toLowerCase().indexOf(terms[0])-20);
    var t=esc((pos?'…':'')+full.slice(pos,pos+110));
    terms.forEach(function(q){ t=t.replace(new RegExp('('+q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','ig'),'<mark>$1</mark>'); });
    return '<button class="sr" data-t="'+b.start+'"><span class="k">'+(KIND_LABEL[b.kind]||b.kind)+'</span>'
      +'<span class="t">'+t+'</span><span class="ts">'+(b.est?'~':'')+fmt(b.start)+'</span></button>';
  }).join('');
  box.classList.add('open');
  $$('.sr',box).forEach(function(el){el.addEventListener('click',function(e){ e.stopPropagation(); box.classList.remove('open'); seek(Number(el.dataset.t)); });});
}
var tmr; $('#q').addEventListener('input',function(e){clearTimeout(tmr);var v=e.target.value;tmr=setTimeout(function(){search(v);},120);});

document.addEventListener('keydown',function(e){
  var tag=e.target.tagName;
  if(e.key==='Escape'){ $('#results').classList.remove('open'); closeModal(); $('#qcard').hidden=true; if(/^(INPUT|TEXTAREA|SELECT)$/.test(tag)) e.target.blur(); return; }
  if(/^(INPUT|TEXTAREA|SELECT)$/.test(tag)||e.ctrlKey||e.metaKey||e.altKey) return;
  var k=e.key, handled=true, t=V.currentTime||0, i=segAt(t);
  if(k==='/') $('#q').focus();
  else if(k===' '&&tag!=='BUTTON'){ if(V.paused){var p=V.play();if(p&&p.catch)p.catch(function(){});} else V.pause(); }
  else if(k==='ArrowLeft') skipBy(-5);
  else if(k==='ArrowRight') skipBy(5);
  else if(k==='j'||k==='J') seek(t-S[i].start>3||i===0?S[i].start:S[i-1].start,!V.paused);
  else if(k==='k'||k==='K'){ if(i<S.length-1) seek(S[i+1].start,!V.paused); else toast('已是最後一段'); }
  else if(k==='a'||k==='A') pressA();
  else if(k==='b'||k==='B') pressB();
  else if(k==='[') stepRate(-1);
  else if(k===']') stepRate(1);
  else if(k==='c'||k==='C') cycleSub();
  else if(k==='q'||k==='Q') askAI();
  else if(k==='s'||k==='S') capture();
  else if(k==='?') showHelp();
  else handled=false;
  if(handled) e.preventDefault();
});
function drag(handle,apply){ handle.addEventListener('pointerdown',function(e){e.preventDefault();handle.setPointerCapture(e.pointerId);
  var mv=function(m){apply(m);};
  var up=function(){handle.removeEventListener('pointermove',mv);handle.removeEventListener('pointerup',up);};
  handle.addEventListener('pointermove',mv);handle.addEventListener('pointerup',up);}); }
drag($('#vsplit'),function(m){document.body.style.setProperty('--vcol',Math.min(Math.max(m.clientX,280),window.innerWidth-380)+'px');});
drag($('#hsplit'),function(m){var r=$('#notes').getBoundingClientRect();
  document.body.style.setProperty('--srow',Math.min(Math.max(m.clientY-r.top,100),r.height-120)+'px');});

/* -- start-up --------------------------------------------------------------- */
$('#cpane').innerHTML='<h2>章節</h2>'+$('#segnav').innerHTML;
renderQuiz(); renderTriage(); buildTimeline();
setSub(subMode); setQuiz(quizOn); setRate(store.get('l2n:pref:rate',1)); setSkip(false);
setMode(store.get('l2n:pref:mode:'+(mobile()?'m':'d'),mobile()?'chap':'split')); paint(); abTag();
/* ?t=<seconds> (or #t=) deep link: a hub search result opens this page at
   that moment. Without one, offer the position this browser stopped at. */
(function(){ var q=new URLSearchParams(location.search).get('t');
  var h=(location.hash.match(/t=(\d+(?:\.\d+)?)/)||[])[1]; var t=Number(q||h);
  if(!isNaN(t)&&t>0){
    var go=function(){try{V.currentTime=t;}catch(e){} sync(t,true);};
    V.addEventListener('loadedmetadata',go,{once:true});
    if(V.readyState) go();
  } else { var pos=store.get(POS_KEY,0);
    if(pos>5){ var r=$('#resume'); r.dataset.t=pos; r.textContent='上次 '+fmt(pos); r.hidden=false; } }
  sync(isNaN(t)?0:t,true);
})();
"""

FOOTER_NOTE = (
    "點任一行跳播　·　時間前有「~」＝段落內插補的推估值　·　"
    "按 ? 看快捷鍵（C 字幕、Q 問 AI、S 擷取、J/K 換段、A/B 區段重播）"
)


def _json_block(element_id: str, payload: Any) -> str:
    """Embed JSON in a script tag without letting its text close the tag."""
    # ``<`` becomes its JSON escape, so the payload can neither open nor close a
    # tag no matter what a title or a transcript line happens to contain.
    text = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return '<script id="%s" type="application/json">%s</script>' % (element_id, text)


def _stamp(block: Mapping[str, Any]) -> str:
    """The time label. A tilde appears only on an interpolated time."""
    return ("~" if block.get("est") else "") + clock(block["start"])


def _block_html(block: Mapping[str, Any], extra_class: str = "") -> str:
    classes = " ".join(
        part for part in ("blk", extra_class, "est" if block.get("est") else "") if part
    )
    opening = '<span class="qm">「</span>' if block["kind"] == "quote" else ""
    closing = '<span class="qm">」</span>' if block["kind"] == "quote" else ""
    translation = (
        '<span class="zh">%s</span>' % html.escape(str(block["zh"])) if block.get("zh") else ""
    )
    return (
        '<button class="%s" id="b-%s" data-t="%s" data-kind="%s"%s>'
        '<span class="ts">%s</span>%s%s%s%s</button>'
        % (
            classes,
            html.escape(str(block["id"]), quote=True),
            block["start"],
            html.escape(str(block["kind"]), quote=True),
            ' data-est="1"' if block.get("est") else "",
            html.escape(_stamp(block)),
            opening,
            html.escape(str(block["text"])),
            closing,
            translation,
        )
    )


def _novelty_badge(view: Mapping[str, Any]) -> str:
    triage = view.get("triage") or {}
    novelty = triage.get("novelty") if isinstance(triage, Mapping) else None
    if not novelty:
        return ""
    return '<span class="nov nov-%s">%s</span>' % (
        html.escape(str(novelty), quote=True), html.escape(str(novelty))
    )


def render(
    data: Mapping[str, Any],
    segments: Sequence[Mapping[str, Any]],
    blocks: Sequence[Mapping[str, Any]],
    title: str,
    video_rel: str,
    media_dir: str = "",
    links: Optional[Mapping[str, str]] = None,
) -> str:
    """The whole page as one string. Pure: nothing is read or written here.

    ``links`` holds the relative paths the ask-AI text names (``subtitle``,
    ``bilingual``, ``json``); the page turns them into absolute URLs at run
    time, against wherever it is being served from.
    """
    by_segment: Dict[str, List[Mapping[str, Any]]] = {}
    for block in blocks:
        by_segment.setdefault(block["seg"], []).append(block)

    nav: List[str] = []
    summary_html: List[str] = []
    transcript_html: List[str] = []
    for view in segments:
        mine = by_segment.get(view["id"], [])
        triage = view.get("triage") or {}
        known = isinstance(triage, Mapping) and triage.get("novelty") == "已知"
        nav.append(
            '<button class="segcard%s" data-seg="%s" data-t="%s"><b>%s</b>%s%s'
            '<div class="sub">%s-%s</div></button>'
            % (
                " known" if known else "",
                html.escape(str(view["id"]), quote=True), view["start"],
                html.escape(str(view["index"])), html.escape(str(view["title"])),
                _novelty_badge(view),
                clock(view["start"]), clock(view["end"]),
            )
        )
        thumbs = "".join(
            '<img src="%s" loading="lazy" alt="">'
            % html.escape(media_dir + str(frame), quote=True)
            for frame in view["frames"]
        )
        slides = [b for b in mine if b["kind"] == "slide"]
        ocr_html = ""
        if slides:
            ocr_html = (
                '<details class="ocr"><summary>投影片文字 %d 張'
                "（OCR，有誤差，僅供定位與搜尋）</summary>%s</details>"
                % (len(slides), "".join(_block_html(b, "slide") for b in slides))
            )
        quotes = [b for b in mine if b["kind"] == "quote"]
        summary_html.append(
            '<section class="seg" id="seg-%s"><h3><span class="no">%s</span> %s%s</h3>%s'
            '<p class="sum">%s</p>%s%s%s</section>'
            % (
                html.escape(str(view["id"]), quote=True),
                html.escape(str(view["index"])),
                html.escape(str(view["title"])),
                _novelty_badge(view),
                ('<div class="thumbs">%s</div>' % thumbs) if thumbs else "",
                html.escape(str(view["summary"])),
                "".join(_block_html(b) for b in mine if b["kind"] == "summary"),
                "".join(_block_html(b, "quote") for b in quotes),
                ocr_html,
            )
        )
        cues = [b for b in mine if b["kind"] == "transcript"]
        if cues:
            transcript_html.append(
                '<section class="seg"><h3><span class="no">%s</span> %s</h3>%s</section>'
                % (
                    html.escape(str(view["index"])),
                    html.escape(str(view["title"])),
                    "".join(_block_html(b) for b in cues),
                )
            )

    takeaways = "".join(
        "<li>%s</li>" % html.escape(str(item))
        for item in (data.get("takeaways_zh") or [])
    )
    all_links = {"video": video_rel}
    all_links.update({k: v for k, v in (links or {}).items() if v})
    triage_overall = data.get("triage_overall")
    payload = {
        "stem": data.get("stem", ""),
        "schema_version": data.get("schema_version", ""),
        "title": title,
        "duration": data.get("duration_sec") or 0,
        "links": all_links,
        "triage_overall": triage_overall if isinstance(triage_overall, Mapping) else None,
        "questions_zh": [
            q for q in (data.get("questions_zh") or []) if isinstance(q, Mapping)
        ],
        "segments": list(segments),
        "blocks": list(blocks),
    }
    mode_buttons = (
        '  <button class="desktop-only" data-mode="sum">整理稿</button>\n'
        '  <button class="desktop-only" data-mode="split">兩欄</button>\n'
        '  <button class="desktop-only" data-mode="tr">逐字稿</button>\n'
        '  <button class="desktop-only" data-mode="quiz">題目</button>\n'
        '  <button class="desktop-only" data-mode="triage">分流</button>\n'
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-Hant"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
        "<title>" + html.escape(title) + "</title><style>" + CSS + "</style></head><body>\n"
        "<header>\n"
        "  <h1>" + html.escape(title) + "</h1>\n"
        '  <button id="resume" hidden title="回到上次看到的位置"></button>\n'
        + mode_buttons +
        '  <button class="desktop-only" id="autoscroll">自動捲動：開</button>\n'
        '  <button class="desktop-only" id="float">浮動播放器</button>\n'
        '  <span class="spacer desktop-only"></span>\n'
        '  <button class="desktop-only" id="zout">A-</button><button class="desktop-only" id="zin">A+</button>\n'
        '  <button class="askai" title="複製目前位置與上下文，貼到任一 AI（Q）">問 AI</button>\n'
        '  <button class="setbtn mobile-only" title="設定">設定</button>\n'
        '  <button class="desktop-only" id="helpbtn" title="快捷鍵（?）">?</button>\n'
        '  <span class="search"><input id="q" '
        'placeholder="搜尋摘要、原話、投影片與逐字稿（按 / 聚焦）">'
        '<div id="results"></div></span>\n'
        "</header>\n"
        "<main>\n"
        '  <div id="vpane">\n'
        '    <div id="vhead">\n'
        '      <div id="vwrap"><video id="player" controls playsinline preload="metadata" src="'
        + html.escape(video_rel, quote=True) + '"></video>'
        '<div id="subs" hidden></div><div id="flash" hidden></div><div id="qcard" hidden></div></div>\n'
        '      <div id="timeline" title="章節"><div id="tl-track"><div id="tl-ab"></div></div>'
        '<div id="tl-pos"></div><div id="tl-tip"></div></div>\n'
        '      <div id="ctrl"><button id="subbtn">字幕</button><button id="speed">1x</button>'
        '<button id="quizbtn">題目模式</button><button id="capbtn" title="擷取時間與當句字幕（S）">擷取</button>'
        '<button id="fsbtn">全螢幕</button><button class="setbtn desktop-only">設定</button>'
        '<span id="abtag"></span></div>\n'
        '      <div id="now">尚未播放</div>\n'
        "    </div>\n"
        '    <div id="segnav">' + "".join(nav) + "</div>\n"
        "  </div>\n"
        '  <div id="vsplit"></div>\n'
        '  <div id="notes">\n'
        '    <div class="pane" id="cpane"></div>\n'
        '    <div class="pane" id="spane"><h2>整理稿（摘要層）</h2>' + "".join(summary_html)
        + '<section class="seg"><h3>全片重點</h3><ul>' + takeaways + "</ul></section></div>\n"
        '    <div id="hsplit"></div>\n'
        '    <div class="pane" id="tpane"><h2>逐字稿（時間層）</h2>'
        + "".join(transcript_html) + "</div>\n"
        '    <div class="pane" id="qpane"></div>\n'
        '    <div class="pane" id="xpane"></div>\n'
        "  </div>\n"
        "</main>\n"
        '<nav id="tabs"><button data-mode="chap">章節</button><button data-mode="tr">逐字稿</button>'
        '<button data-mode="quiz">題目</button><button data-mode="sum">摘要</button>'
        '<button data-mode="triage">分流</button></nav>\n'
        '<div class="note">' + html.escape(FOOTER_NOTE) + "</div>\n"
        '<div id="toast" role="status"></div>\n'
        '<div id="modal" hidden><div class="box"></div></div>\n'
        + _json_block("viewer-data", payload) + "\n"
        + _json_block("canonical-snapshot", canonical_spine(data)) + "\n"
        "<script>" + JS + "</script></body></html>\n"
    )


# --------------------------------------------------------------------------
# file-system layer
# --------------------------------------------------------------------------
def viewer_path(json_path: Path) -> Path:
    json_path = Path(json_path)
    return json_path.parent / (json_path.stem + VIEWER_SUFFIX)


def preflight(json_path: Path) -> List[PlannedFile]:
    """What ``l2n viewer`` would write. Reads the filesystem, writes nothing."""
    return plan_for([viewer_path(json_path)])


def _source_block(data: Mapping[str, Any]) -> Mapping[str, Any]:
    source = data.get("source")
    return source if isinstance(source, Mapping) else {}


def resolve_video(json_path: Path, data: Mapping[str, Any]) -> Optional[str]:
    """The video reference to put in the page, as a relative path.

    ``source.video`` in a v2 document wins, because the document knows which of
    several recordings it was built from; the sibling scan is the fallback for a
    migrated file whose source block is a guess.
    """
    json_path = Path(json_path)
    name = _source_block(data).get("video")
    if isinstance(name, str) and name and (json_path.parent / name).exists():
        return name
    found = find_sibling(json_path.parent, json_path.stem, VIDEO_EXT)
    if found is not None:
        return found.name
    return name if isinstance(name, str) and name else None


def resolve_subtitle(json_path: Path, data: Mapping[str, Any]) -> Optional[Path]:
    """The subtitle the transcript layer is built from, or None."""
    json_path = Path(json_path)
    subtitle = _source_block(data).get("subtitle")
    name = subtitle.get("path") if isinstance(subtitle, Mapping) else None
    if isinstance(name, str) and name:
        candidate = json_path.parent / name
        if candidate.exists():
            return candidate
    return find_sibling(json_path.parent, json_path.stem, SUBTITLE_EXT)


def transcript_source(
    json_path: Path, data: Mapping[str, Any], subtitle: Optional[Path]
) -> Tuple[List[Cue], Optional[List[str]], str]:
    """``(cues, translations, label)`` for the transcript layer.

    The document's own ``lines`` win: they are already de-duplicated and carry
    ``zh``. Otherwise the subtitle file is read, and a ``<stem>.zh.srt`` beside
    it supplies translations when it has exactly the same cues and times.
    """
    lines = data.get("lines")
    if isinstance(lines, list) and lines:
        cues: List[Cue] = []
        zh: List[str] = []
        for line in lines:
            if not isinstance(line, Mapping):
                continue
            t, end = _real_time(line.get("t")), _real_time(line.get("end"))
            text = str(line.get("text") or "").strip()
            if t is None or end is None or not text:
                continue
            cues.append(Cue(start=t, end=end, text=text))
            zh.append(str(line.get("zh") or ""))
        if cues:
            return cues, (zh if any(zh) else None), "lines"
    if subtitle is None or not Path(subtitle).exists():
        return [], None, ""
    cues = parse_srt(subtitle)
    translated = Path(subtitle).with_name(Path(subtitle).stem + ".zh.srt")
    if translated.is_file():
        other = parse_srt(translated)
        if [(c.start, c.end) for c in other] == [(c.start, c.end) for c in cues]:
            return cues, [c.text for c in other], translated.name
    return cues, None, Path(subtitle).name


def frame_times(json_path: Path) -> Dict[str, float]:
    """Frame path -> capture time, from ``<stem>.frames.json`` when it exists.

    A filename only approximates the time (and a frame captured twice in one
    second gets a letter suffix); the manifest records the real one.
    """
    from lecture2notes.frames.manifest import read_manifest

    manifest = Path(json_path).with_name(Path(json_path).stem + ".frames.json")
    if not manifest.is_file():
        return {}
    try:
        rows = read_manifest(manifest)
    except (ValueError, OSError):
        return {}
    out: Dict[str, float] = {}
    for row in rows:
        if isinstance(row, Mapping) and row.get("frame") and _real_time(row.get("timestamp_sec")) is not None:
            out[str(row["frame"]).replace("\\", "/")] = float(row["timestamp_sec"])
    return out


def page_links(json_path: Path, subtitle: Optional[Path]) -> Dict[str, str]:
    """Relative paths of the files the ask-AI text names, when they exist."""
    json_path = Path(json_path)
    links: Dict[str, str] = {"json": json_path.name}
    if subtitle is not None and Path(subtitle).exists():
        subtitle = Path(subtitle)
        if subtitle.parent.resolve() == json_path.parent.resolve():
            links["subtitle"] = subtitle.name
            bilingual = subtitle.with_name(subtitle.stem + ".bi.srt")
            if bilingual.is_file():
                links["bilingual"] = bilingual.name
    return links


def build(
    json_path: Path,
    data: Mapping[str, Any],
    out: Optional[Path] = None,
    title: Optional[str] = None,
    video: Optional[str] = None,
    subtitle: Optional[Path] = None,
) -> Dict[str, Any]:
    """Write ``<stem>.viewer.html`` beside the document and its video.

    Every path in the page is relative, so the folder can be copied or served
    from anywhere; the page builds absolute URLs from its own address.
    """
    json_path = Path(json_path)
    subtitle_path = Path(subtitle) if subtitle else resolve_subtitle(json_path, data)
    cues, zh, source = transcript_source(json_path, data, subtitle_path)
    segments, blocks = build_blocks(data, cues, zh, frame_times(json_path))
    video_rel = video or resolve_video(json_path, data) or ""
    page = render(
        data, segments, blocks,
        title or str(data.get("title") or json_path.stem),
        video_rel,
        links=page_links(json_path, subtitle_path),
    )
    destination = Path(out) if out else viewer_path(json_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8", newline="\n")
    return {
        "path": destination,
        "segments": len(segments),
        "blocks": len(blocks),
        "estimated": estimated_block_count(blocks),
        "cues": len(cues),
        "translated": sum(1 for line in (zh or []) if line),
        "transcript": source,
        "video": video_rel,
        "subtitle": str(subtitle_path) if subtitle_path else "",
    }


__all__ = [
    "CSS",
    "JS",
    "KINDS",
    "SUBTITLE_EXT",
    "VIDEO_EXT",
    "VIEWER_SUFFIX",
    "build",
    "build_blocks",
    "bullet_text",
    "canonical_spine",
    "clock",
    "estimated_block_count",
    "find_sibling",
    "frame_times",
    "page_links",
    "parse_srt",
    "parse_srt_text",
    "preflight",
    "read_subtitle_text",
    "render",
    "resolve_subtitle",
    "resolve_video",
    "segment_view",
    "transcript_source",
    "viewer_path",
]
