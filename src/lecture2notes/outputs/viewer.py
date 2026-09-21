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
    }


def _real_time(value: Any) -> Optional[float]:
    """A usable timecode, or None. ``True`` is not a timecode."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def build_blocks(
    data: Mapping[str, Any],
    cues: Sequence[Cue],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return ``(segments, blocks)``: every clickable line with its time.

    Slide OCR text becomes its own block timed from the frame filename, so
    clicking a line of slide text jumps to the moment that slide appeared.
    Quotes carry a real ``t`` of their own and are never marked estimated.
    """
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
            at = float(frame_seconds(entry.get("frame", ""), start))
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
        blocks.append({
            "id": "t%d" % position,
            "seg": owner["id"] if owner else "",
            "kind": "transcript",
            "start": cue.start, "end": cue.end,
            "text": cue.text, "est": False,
        })
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
CSS = """
:root{--bg:#0f1115;--panel:#171a21;--line:#272c37;--fg:#e6e9ef;--dim:#9aa3b2;--accent:#4da3ff;--hit:#f5c451}
*{box-sizing:border-box}
html{height:100%;overflow:hidden}
body{margin:0;font:16px/1.65 "Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;background:var(--bg);color:var(--fg);height:100vh;display:flex;flex-direction:column;overflow:hidden}
header{flex:0 0 auto;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;padding:.5rem .8rem;background:var(--panel);border-bottom:1px solid var(--line);z-index:40}
header h1{font-size:1rem;margin:0 .6rem 0 0;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:34vw}
button{background:#222833;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:.25rem .6rem;cursor:pointer;font-size:.85rem;font-family:inherit}
button:hover{border-color:var(--accent)}
button.active{background:var(--accent);color:#08101c;border-color:var(--accent);font-weight:600}
.spacer{flex:1}
.search{position:relative}
.search input{background:#0d1016;border:1px solid var(--line);color:var(--fg);border-radius:6px;padding:.3rem .6rem;width:15rem;font-size:.85rem;font-family:inherit}
#results{position:absolute;top:110%;right:0;width:34rem;max-height:60vh;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px;display:none;z-index:50}
#results.open{display:block}
.sr{display:grid;grid-template-columns:5.5rem 1fr 4rem;gap:.4rem;width:100%;text-align:left;background:none;border:0;border-bottom:1px solid var(--line);padding:.4rem .6rem;border-radius:0}
.sr:hover{background:#1e2530}
.sr .k{color:var(--dim);font-size:.72rem}
.sr .t{font-size:.82rem}
.sr .ts{color:var(--accent);font-size:.75rem;text-align:right}
.count{padding:.3rem .6rem;color:var(--dim);font-size:.75rem}
main{flex:1 1 auto;min-height:0;overflow:hidden;display:grid;grid-template-columns:var(--vcol,44%) 6px 1fr}
#vpane{padding:.6rem;min-height:0;overflow:hidden;display:flex;flex-direction:column}
#vhead{flex:0 0 auto}
video{width:100%;background:#000;border-radius:8px}
#now{font-size:.8rem;color:var(--dim);margin-top:.4rem;min-height:1.4em}
#segnav{flex:1 1 auto;min-height:0;overflow:auto;margin-top:.7rem;display:flex;flex-direction:column;gap:.3rem}
.segcard{text-align:left;padding:.4rem .6rem;font-size:.85rem;line-height:1.4}
.segcard.on{border-color:var(--accent);background:#1b2635}
.segcard b{color:var(--accent);margin-right:.4rem;font-weight:600}
#vsplit,#hsplit{background:var(--line);cursor:col-resize}
#hsplit{cursor:row-resize;height:6px}
#notes{display:grid;grid-template-rows:var(--srow,45%) 6px 1fr;min-width:0;min-height:0;overflow:hidden}
.pane{overflow:auto;min-height:0;padding:.6rem .9rem}
.pane h2{font-size:.8rem;color:var(--dim);margin:.2rem 0 .6rem;font-weight:600;letter-spacing:.05em}
body.only-sum #hsplit,body.only-sum #tpane{display:none}
body.only-sum #notes{grid-template-rows:1fr}
body.only-tr #hsplit,body.only-tr #spane{display:none}
body.only-tr #notes{grid-template-rows:1fr}
.seg{margin-bottom:1.1rem}
.seg>h3{font-size:.95rem;margin:.2rem 0 .3rem}
.seg>h3 .no{color:var(--accent)}
.sum{color:var(--dim);font-size:.85rem;margin:0 0 .4rem}
.blk{display:block;width:100%;text-align:left;background:none;border:0;border-left:3px solid transparent;border-radius:0;padding:.18rem .5rem;color:var(--fg);font-size:.9rem;line-height:1.6}
.blk:hover{background:#1c222c;border-left-color:var(--dim)}
.blk.on{background:#233246;border-left-color:var(--hit)}
.blk .ts{color:var(--accent);font-size:.75rem;margin-right:.45rem;font-variant-numeric:tabular-nums}
.blk.est .ts{color:var(--dim)}
.blk.quote{color:#cfd6e2;border-left-color:#4b5668}
.blk.quote .qm{color:var(--dim)}
#tpane .blk{font-size:.86rem;padding:.1rem .5rem}
.thumbs{display:flex;gap:.3rem;flex-wrap:wrap;margin:.3rem 0 .5rem}
.thumbs img{height:72px;border-radius:4px;border:1px solid var(--line);cursor:zoom-in}
.ocr{margin:.3rem 0 .2rem}
.ocr>summary{cursor:pointer;color:var(--dim);font-size:.78rem;padding:.15rem .5rem}
.blk.slide{white-space:pre-wrap;font-size:.8rem;color:var(--dim);border-left-color:#3a4557}
.blk.slide:hover{color:var(--fg)}
.note{flex:0 0 auto;font-size:.72rem;color:var(--dim);padding:.25rem .9rem;background:var(--panel);border-top:1px solid var(--line);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
mark{background:var(--hit);color:#111}
body.float #vpane{position:fixed;right:1rem;bottom:1rem;width:var(--fw,26rem);z-index:60;background:var(--panel);border:1px solid var(--line);border-radius:10px;box-shadow:0 8px 30px #0009;resize:both;overflow:auto;max-height:80vh}
body.float #segnav{display:none}
body.float main{grid-template-columns:0 0 1fr}
@media(max-width:900px){main{grid-template-columns:1fr;grid-template-rows:auto 0 1fr}#vsplit{display:none}}
"""

JS = r"""
var DATA = JSON.parse(document.getElementById('viewer-data').textContent);
var S = DATA.segments;
var B = DATA.blocks.slice().sort(function(a,b){return a.start-b.start;});
var KIND_LABEL = {summary:'\u6458\u8981', transcript:'\u9010\u5b57', slide:'\u6295\u5f71\u7247', quote:'\u539f\u8a71'};
var auto=true, suspend=0, progScroll=false, activeSeg=S.length?S[0].id:'';
var $=function(s,r){return (r||document).querySelector(s);};
var $$=function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s));};
var fmt=function(t){t=Math.max(0,Math.floor(t||0));var h=Math.floor(t/3600),m=Math.floor(t%3600/60),s=t%60;
  return (h?h+':'+String(m).padStart(2,'0'):String(m))+':'+String(s).padStart(2,'0');};
var V=$('#player');

/* preload="metadata" has not finished when the page opens, and a currentTime
   set before that is dropped on the floor, so a deep link waits for
   loadedmetadata instead of assuming the seek took. */
function seek(t,play){
  var go=function(){ try{V.currentTime=t;}catch(e){}
    if(play!==false){ var p=V.play(); if(p&&p.catch){p.catch(function(){});} } };
  if(V.readyState===0){ V.addEventListener('loadedmetadata',go,{once:true}); V.load(); }
  else { go(); }
  sync(t,true);
}

function sync(t,force){
  var hits=B.filter(function(b){return b.start-0.15<=t && t<b.end+0.15;});
  if(!hits.length) return;
  $$('.blk.on').forEach(function(e){e.classList.remove('on');});
  var lead=hits.filter(function(b){return b.kind==='summary';})[0]||hits[0];
  if(lead.seg!==activeSeg){ activeSeg=lead.seg;
    $$('.segcard').forEach(function(e){e.classList.toggle('on',e.dataset.seg===activeSeg);});
    var c=$('.segcard.on'); if(c&&(force||follow())) scrollInto(c); }
  $('#now').textContent=fmt(lead.start)+'  '+lead.text.slice(0,60);
  hits.forEach(function(h){
    var el=document.getElementById('b-'+h.id); if(!el) return;
    el.classList.add('on');
    if(force||follow()) scrollInto(el);
  });
}
function follow(){ return auto && Date.now()>suspend && !V.paused; }
function scrollInto(el){
  var pane=el.closest('.pane, #segnav'); if(!pane) return;
  var target=pane.scrollTop+(el.getBoundingClientRect().top-pane.getBoundingClientRect().top)-pane.clientHeight*0.42;
  progScroll=true; pane.scrollTo({top:Math.max(0,target),behavior:'auto'});
  setTimeout(function(){progScroll=false;},80);
}
/* Scrolling by hand suspends the follow for five seconds: without it, looking
   back one paragraph yanks the reader forward again. */
$$('.pane, #segnav').forEach(function(p){p.addEventListener('wheel',function(){
  if(!progScroll&&auto&&!V.paused){ suspend=Date.now()+5000; paint(); }},{passive:true});});
function paint(){ var b=$('#autoscroll');
  b.textContent='\u81ea\u52d5\u6372\u52d5\uff1a'+(!auto?'\u95dc':(Date.now()<suspend?'\u66ab\u505c':'\u958b'));
  b.classList.toggle('active',auto&&Date.now()>=suspend); }
setInterval(paint,1000);

V.addEventListener('timeupdate',function(){ if(!V.paused) sync(V.currentTime,false); });
document.addEventListener('click',function(e){
  var blk=e.target.closest('.blk'); if(blk){ seek(Number(blk.dataset.t)); return; }
  var card=e.target.closest('.segcard'); if(card){ seek(Number(card.dataset.t)); return; }
  var m=e.target.closest('[data-mode]'); if(m){ setMode(m.dataset.mode); return; }
  var img=e.target.closest('.thumbs img'); if(img){ window.open(img.src,'_blank'); return; }
  if(!e.target.closest('.search')) $('#results').classList.remove('open');
});
function setMode(m){ document.body.classList.toggle('only-sum',m==='sum');
  document.body.classList.toggle('only-tr',m==='tr');
  $$('[data-mode]').forEach(function(b){b.classList.toggle('active',b.dataset.mode===m);}); }
$('#autoscroll').addEventListener('click',function(){auto=!auto;suspend=0;paint();});
$('#float').addEventListener('click',function(e){document.body.classList.toggle('float');e.target.classList.toggle('active');});
var fs=1; var zoom=function(d){fs=Math.min(1.7,Math.max(0.85,fs+d));document.documentElement.style.fontSize=(16*fs)+'px';};
$('#zin').addEventListener('click',function(){zoom(0.1);}); $('#zout').addEventListener('click',function(){zoom(-0.1);});

/* One search box over every layer: summary, quotes, slide text, transcript. */
var esc=function(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});};
function search(qs){
  var box=$('#results');
  var terms=(qs||'').trim().toLowerCase().split(/\s+/).filter(Boolean);
  if(!terms.length){ box.classList.remove('open'); box.innerHTML=''; return; }
  var hits=B.filter(function(b){return terms.every(function(t){return b.text.toLowerCase().indexOf(t)>=0;});}).slice(0,80);
  if(!hits.length){ box.innerHTML='<div class="count">\u7121\u7b26\u5408\u7d50\u679c</div>'; box.classList.add('open'); return; }
  box.innerHTML='<div class="count">'+hits.length+' \u7b46</div>'+hits.map(function(b){
    var pos=Math.max(0,b.text.toLowerCase().indexOf(terms[0])-20);
    var t=esc((pos?'\u2026':'')+b.text.slice(pos,pos+110));
    terms.forEach(function(q){ t=t.replace(new RegExp('('+q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','ig'),'<mark>$1</mark>'); });
    return '<button class="sr" data-t="'+b.start+'"><span class="k">'+(KIND_LABEL[b.kind]||b.kind)+'</span>'
      +'<span class="t">'+t+'</span><span class="ts">'+(b.est?'~':'')+fmt(b.start)+'</span></button>';
  }).join('');
  box.classList.add('open');
  $$('.sr',box).forEach(function(el){el.addEventListener('click',function(){ box.classList.remove('open'); seek(Number(el.dataset.t)); });});
}
var tmr; $('#q').addEventListener('input',function(e){clearTimeout(tmr);var v=e.target.value;tmr=setTimeout(function(){search(v);},120);});
document.addEventListener('keydown',function(e){
  if(e.key==='/'&&!/^(INPUT|TEXTAREA)$/.test(e.target.tagName)){e.preventDefault();$('#q').focus();}
  if(e.key==='Escape'){$('#results').classList.remove('open');} });
function drag(handle,apply){ handle.addEventListener('pointerdown',function(e){e.preventDefault();handle.setPointerCapture(e.pointerId);
  var mv=function(m){apply(m);};
  var up=function(){handle.removeEventListener('pointermove',mv);handle.removeEventListener('pointerup',up);};
  handle.addEventListener('pointermove',mv);handle.addEventListener('pointerup',up);}); }
drag($('#vsplit'),function(m){document.body.style.setProperty('--vcol',Math.min(Math.max(m.clientX,280),window.innerWidth-380)+'px');});
drag($('#hsplit'),function(m){var r=$('#notes').getBoundingClientRect();
  document.body.style.setProperty('--srow',Math.min(Math.max(m.clientY-r.top,100),r.height-120)+'px');});
setMode('split'); paint();
/* ?t=<seconds> deep link: a hub search result opens this page at that moment. */
(function(){ var t=Number(new URLSearchParams(location.search).get('t'));
  if(!isNaN(t)&&t>0){
    var go=function(){try{V.currentTime=t;}catch(e){} sync(t,true);};
    V.addEventListener('loadedmetadata',go,{once:true});
    if(V.readyState) go();
  } })();
"""

FOOTER_NOTE = (
    "點任一行跳播，播放時各層同時高亮跟隨　·　"
    "時間前有「~」＝段落內插補的推估值，其餘為真實時間碼"
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
    return (
        '<button class="%s" id="b-%s" data-t="%s" data-kind="%s"%s>'
        '<span class="ts">%s</span>%s%s%s</button>'
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
        )
    )


def render(
    data: Mapping[str, Any],
    segments: Sequence[Mapping[str, Any]],
    blocks: Sequence[Mapping[str, Any]],
    title: str,
    video_rel: str,
    media_dir: str = "",
) -> str:
    """The whole page as one string. Pure: nothing is read or written here."""
    by_segment: Dict[str, List[Mapping[str, Any]]] = {}
    for block in blocks:
        by_segment.setdefault(block["seg"], []).append(block)

    nav: List[str] = []
    summary_html: List[str] = []
    transcript_html: List[str] = []
    for view in segments:
        mine = by_segment.get(view["id"], [])
        nav.append(
            '<button class="segcard" data-seg="%s" data-t="%s"><b>%s</b>%s'
            '<div style="color:var(--dim);font-size:.75rem">%s-%s</div></button>'
            % (
                html.escape(str(view["id"]), quote=True), view["start"],
                html.escape(str(view["index"])), html.escape(str(view["title"])),
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
            '<section class="seg" id="seg-%s"><h3><span class="no">%s</span> %s</h3>%s'
            '<p class="sum">%s</p>%s%s%s</section>'
            % (
                html.escape(str(view["id"]), quote=True),
                html.escape(str(view["index"])),
                html.escape(str(view["title"])),
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
    payload = {
        "stem": data.get("stem", ""),
        "schema_version": data.get("schema_version", ""),
        "segments": list(segments),
        "blocks": list(blocks),
    }
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-Hant"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        "<title>" + html.escape(title) + "</title><style>" + CSS + "</style></head><body>\n"
        "<header>\n"
        "  <h1>" + html.escape(title) + "</h1>\n"
        '  <button data-mode="sum">整理稿</button>\n'
        '  <button data-mode="split">兩欄</button>\n'
        '  <button data-mode="tr">逐字稿</button>\n'
        '  <button id="autoscroll">自動捲動：開</button>\n'
        '  <button id="float">浮動播放器</button>\n'
        '  <span class="spacer"></span>\n'
        '  <button id="zout">A-</button><button id="zin">A+</button>\n'
        '  <span class="search"><input id="q" '
        'placeholder="搜尋摘要、原話、投影片與逐字稿（按 / 聚焦）">'
        '<div id="results"></div></span>\n'
        "</header>\n"
        "<main>\n"
        '  <div id="vpane">\n'
        '    <div id="vhead">\n'
        '      <video id="player" controls preload="metadata" src="'
        + html.escape(video_rel, quote=True) + '"></video>\n'
        '      <div id="now">尚未播放</div>\n'
        "    </div>\n"
        '    <div id="segnav">' + "".join(nav) + "</div>\n"
        "  </div>\n"
        '  <div id="vsplit"></div>\n'
        '  <div id="notes">\n'
        '    <div class="pane" id="spane"><h2>整理稿（摘要層）</h2>' + "".join(summary_html)
        + '<section class="seg"><h3>全片重點</h3><ul>' + takeaways + "</ul></section></div>\n"
        '    <div id="hsplit"></div>\n'
        '    <div class="pane" id="tpane"><h2>逐字稿（時間層）</h2>'
        + "".join(transcript_html) + "</div>\n"
        "  </div>\n"
        "</main>\n"
        '<div class="note">' + html.escape(FOOTER_NOTE) + "</div>\n"
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


def build(
    json_path: Path,
    data: Mapping[str, Any],
    out: Optional[Path] = None,
    title: Optional[str] = None,
    video: Optional[str] = None,
    subtitle: Optional[Path] = None,
) -> Dict[str, Any]:
    """Write ``<stem>.viewer.html`` and report what went into it."""
    json_path = Path(json_path)
    subtitle_path = Path(subtitle) if subtitle else resolve_subtitle(json_path, data)
    cues = parse_srt(subtitle_path) if subtitle_path and subtitle_path.exists() else []
    segments, blocks = build_blocks(data, cues)
    video_rel = video or resolve_video(json_path, data) or ""
    page = render(
        data, segments, blocks,
        title or str(data.get("title") or json_path.stem),
        video_rel,
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
    "parse_srt",
    "parse_srt_text",
    "preflight",
    "read_subtitle_text",
    "render",
    "resolve_subtitle",
    "resolve_video",
    "segment_view",
    "viewer_path",
]
