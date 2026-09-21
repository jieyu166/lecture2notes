"""Course hub: lecture cards, a cross-lecture search index, and the page.

Ported from rad-workflow skills/lecture-to-notes/scripts/build_course_hub.py.
Inspired by drpwchen/lecture-to-notes scripts/export_web.py @79053a3

A course is a folder of viewers, and without a hub the only way to find anything
is by filename. The hub does the two things a single viewer cannot: show the
lectures as cards, and search across all of them, with every hit deep-linking to
``<viewer>?t=<seconds>`` so the answer opens at the moment it was said.

The index covers slide OCR and quoted speech as well as titles and bullets,
because a term that was only ever on a slide is exactly the term nobody can find
otherwise.

Every relative link the page emits is verified against the filesystem after the
write. A hub full of dead links is worse than no hub: it looks like the lecture
is there and is not, and nothing else in the pipeline would notice.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import quote, unquote

from lecture2notes.frames.manifest import frame_seconds
from lecture2notes.outputs.plan import PlannedFile, plan_for

#: Overrides file name, sitting in the course folder.
TITLES_FILE = "_titles.json"
#: Optional course summary, sitting beside it.
COURSE_FILE = "_course.json"

#: What the two summary rows are called on the page.
COURSE_QUESTION_LABEL = "本系列在回答的問題"
COURSE_START_LABEL = "最該先看的一場"

#: What a series says when its talks share no through-line. Saying it is the
#: answer; leaving the summary empty leaves the reader to find that out by
#: opening every card.
NO_COMMON_THREAD = "本系列各場主題獨立，無共同主線"
DERIVED_SUFFIXES = (".frames.json", ".frames_ocr.json", ".corrections.json")
#: Where an unnumbered lecture sorts: after every numbered one.
UNNUMBERED_SORT_KEY = "zz"
#: The generated page's filename, in the reader's language.
HUB_FILENAME = "課程首頁.html"

#: ``<course>-<no>-<speaker with a title>-<topic>``
NAMED_PATTERN = re.compile(
    r"^(\d+)-(\d+)-([^-]+?醫師|[^-]+?主任|[^-]+?部長|[^-]+?教授)-(.+)$"
)
NUMBERED_PATTERN = re.compile(r"^\d+-(\d+)-(.+)$")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def parse_name(stem: str) -> Dict[str, str]:
    """Pull number, speaker and topic out of a filename.

    When the pattern does not match, the whole stem becomes the topic. Guessing
    harder to make the layout tidy would put wrong names on cards.
    """
    match = NAMED_PATTERN.match(stem)
    if match:
        return {"no": match.group(2), "speaker": match.group(3), "topic": match.group(4)}
    match = NUMBERED_PATTERN.match(stem)
    if match:
        return {"no": match.group(1), "speaker": "", "topic": match.group(2)}
    return {"no": "", "speaker": "", "topic": stem}


def load_titles(folder: Path) -> Dict[str, Dict[str, str]]:
    """Read the optional ``_titles.json`` override map.

    Shape: ``{"<stem>": {"no": "01", "speaker": "...", "topic": "..."}}``.
    A missing or unparseable file means no overrides, not a failure: the hub must
    still build from filenames alone.
    """
    path = Path(folder) / TITLES_FILE
    if not path.exists():
        return {}
    try:
        data = load_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_course(folder: Path) -> Dict[str, Any]:
    """Read the optional ``_course.json`` summary.

    Shape: ``{"question": "...", "start_with": "...", "no_common_thread": false}``.
    Absent, unparseable or not an object all mean "no summary", never a failure:
    a hub that refuses to build because a hand-written sidecar has a stray comma
    is worse than a hub without the sidecar.
    """
    path = Path(folder) / COURSE_FILE
    if not path.exists():
        return {}
    try:
        data = load_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def course_lines(course: Mapping[str, Any]) -> List[tuple]:
    """The summary rows as ``(label, text)``, in the order the page shows them.

    A course of lectures with no stated question is a folder, not a course, so
    the summary is never allowed to be silently empty: a series whose talks
    genuinely share nothing says so, in as many words, rather than leaving the
    reader to work it out one card at a time.
    """
    if not isinstance(course, Mapping):
        return []
    rows: List[tuple] = []
    question = str(course.get("question") or "").strip()
    if course.get("no_common_thread") is True:
        rows.append((COURSE_QUESTION_LABEL, NO_COMMON_THREAD))
    elif question:
        rows.append((COURSE_QUESTION_LABEL, question))
    start_with = str(course.get("start_with") or "").strip()
    if start_with:
        rows.append((COURSE_START_LABEL, start_with))
    return rows


def render_course(course: Mapping[str, Any]) -> str:
    """The summary block that sits above the cards, or an empty string."""
    rows = course_lines(course)
    if not rows:
        return ""
    body = "".join(
        "<div class=\"row\"><dt>%s</dt><dd>%s</dd></div>"
        % (html.escape(label), html.escape(text))
        for label, text in rows
    )
    return '<section class="course"><dl>%s</dl></section>\n' % body


def apply_titles(stem: str, titles: Mapping[str, Any]) -> Dict[str, str]:
    """Derive a card's fields from the filename, then let the override win.

    Only the keys present in the override are replaced, so a file can fix just
    the topic and keep the derived number.
    """
    card = parse_name(stem)
    override = titles.get(stem) or {}
    if isinstance(override, Mapping):
        for key in ("no", "speaker", "topic"):
            if key in override:
                card[key] = str(override[key])
    return card


def card_sort_key(card: Mapping[str, Any]) -> tuple:
    """Order by ``(no, stem)`` compared as strings, as the contract states."""
    return (card.get("no") or UNNUMBERED_SORT_KEY, card.get("stem") or "")


def is_lecture_json(path: Path) -> bool:
    name = Path(path).name
    return not name.startswith("_") and not name.endswith(DERIVED_SUFFIXES)


def build_card(
    json_path: Path, data: Mapping[str, Any], titles: Mapping[str, Any]
) -> Dict[str, Any]:
    """One lecture's card data."""
    json_path = Path(json_path)
    segments = data.get("segments") or []
    card = apply_titles(json_path.stem, titles)
    frames = [frame for segment in segments for frame in (segment.get("frames") or [])]
    card.update({
        "stem": json_path.stem,
        "viewer": "%s.viewer.html" % json_path.stem,
        "segments": segments,
        "frames": frames,
        "thumb": frames[0] if frames else None,
        "minutes": round(float(segments[-1].get("end_sec") or 0) / 60) if segments else 0,
        "summary": (data.get("overall_summary_zh") or "")[:150],
        "takeaways": data.get("takeaways_zh") or [],
    })
    return card


def collect(folder: Path, require_viewer: bool = True) -> List[Dict[str, Any]]:
    """Every lecture in the folder, as sorted cards.

    ``require_viewer`` is off for the hub itself: a lecture whose viewer has not
    been built yet must still produce a card, so that the link check can name the
    missing file instead of the lecture quietly vanishing from the page.
    """
    folder = Path(folder)
    titles = load_titles(folder)
    cards: List[Dict[str, Any]] = []
    for json_path in sorted(folder.glob("*.json")):
        if not is_lecture_json(json_path):
            continue
        if require_viewer and not (folder / ("%s.viewer.html" % json_path.stem)).exists():
            continue
        try:
            data = load_json(json_path)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, Mapping) or not (data.get("segments") or []):
            continue
        cards.append(build_card(json_path, data, titles))
    return sorted(cards, key=card_sort_key)


def _bullet_text(bullet: Any) -> str:
    if isinstance(bullet, Mapping):
        return str(bullet.get("text") or "")
    return str(bullet)


def build_index(cards: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The cross-lecture search index.

    Each row says which viewer to open, at which second, from which layer, and
    with what text. Slide rows carry the frame's own time; everything else
    carries the segment's start, which is what the deep link needs.
    """
    index: List[Dict[str, Any]] = []
    for card in cards:
        for takeaway in card.get("takeaways") or []:
            index.append({
                "v": card["viewer"], "no": card.get("no", ""), "t": 0,
                "k": "takeaway", "x": str(takeaway),
            })
        for segment in card.get("segments") or []:
            if not isinstance(segment, Mapping):
                continue
            second = int(float(segment.get("start_sec") or 0))
            index.append({
                "v": card["viewer"], "no": card.get("no", ""), "t": second,
                "k": "segment", "x": str(segment.get("title") or ""),
            })
            for bullet in segment.get("bullets_zh") or []:
                text = _bullet_text(bullet).strip()
                if not text:
                    continue
                index.append({
                    "v": card["viewer"], "no": card.get("no", ""), "t": second,
                    "k": "bullet", "x": text,
                })
            for quote_item in segment.get("quotes_zh") or []:
                text = str(
                    (quote_item.get("text") if isinstance(quote_item, Mapping) else quote_item)
                    or ""
                ).strip()
                if not text:
                    continue
                at = quote_item.get("t") if isinstance(quote_item, Mapping) else None
                index.append({
                    "v": card["viewer"], "no": card.get("no", ""),
                    "t": int(float(at)) if isinstance(at, (int, float))
                    and not isinstance(at, bool) else second,
                    "k": "quote", "x": text,
                })
            for entry in segment.get("frame_ocr") or []:
                if not isinstance(entry, Mapping):
                    continue
                text = (entry.get("text") or "").strip()
                if not text:
                    continue
                index.append({
                    "v": card["viewer"], "no": card.get("no", ""),
                    "t": int(frame_seconds(entry.get("frame", ""), second)),
                    "k": "slide", "x": text,
                })
    return index


def missing_links(folder: Path, hrefs: Sequence[str]) -> List[str]:
    """Which relative links in the hub point at a file that is not there.

    Links are percent-decoded first, because the href is encoded and the file on
    disk is not.
    """
    folder = Path(folder)
    missing: List[str] = []
    for href in hrefs:
        if "://" in href or href.startswith("#"):
            continue
        target = folder / unquote(href.split("#")[0].split("?")[0])
        if not target.exists() and href not in missing:
            missing.append(href)
    return missing


# --------------------------------------------------------------------------
# page template
# --------------------------------------------------------------------------
CSS = """
:root{--bg:#0f1115;--panel:#171a21;--line:#272c37;--fg:#e6e9ef;--dim:#9aa3b2;--accent:#4da3ff;--hit:#f5c451}
*{box-sizing:border-box}
body{margin:0;font:16px/1.7 "Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;background:var(--bg);color:var(--fg)}
header{padding:1.4rem 1.6rem .8rem;border-bottom:1px solid var(--line);background:var(--panel)}
h1{margin:0 0 .2rem;font-size:1.5rem}
.meta{color:var(--dim);font-size:.85rem}
.searchwrap{margin-top:.9rem;position:relative;max-width:44rem}
#q{width:100%;background:#0d1016;border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:.55rem .8rem;font-size:.95rem;font-family:inherit}
#results{position:absolute;top:108%;left:0;right:0;max-height:66vh;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;display:none;z-index:20;box-shadow:0 10px 40px #000a}
#results.open{display:block}
.sr{display:grid;grid-template-columns:3rem 4.5rem 1fr 4rem;gap:.5rem;align-items:baseline;width:100%;text-align:left;background:none;border:0;border-bottom:1px solid var(--line);padding:.45rem .7rem;color:var(--fg);cursor:pointer;font:inherit;font-size:.85rem}
.sr:hover{background:#1e2530}
.sr .no{color:var(--accent);font-weight:600}
.sr .k{color:var(--dim);font-size:.75rem}
.sr .ts{color:var(--accent);font-size:.78rem;text-align:right}
.count{padding:.4rem .7rem;color:var(--dim);font-size:.78rem}
.course{margin:1.3rem 1.6rem 0;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:.9rem 1.1rem}
.course dl{margin:0}
.course .row{display:grid;grid-template-columns:9rem 1fr;gap:.6rem;padding:.35rem 0}
.course dt{color:var(--dim);font-size:.82rem}
.course dd{margin:0;font-size:.92rem;line-height:1.7}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(21rem,1fr));gap:1rem;padding:1.3rem 1.6rem 3rem}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;text-decoration:none;color:inherit;display:flex;flex-direction:column;transition:border-color .12s}
.card:hover{border-color:var(--accent)}
.card img{width:100%;aspect-ratio:16/9;object-fit:cover;background:#000;border-bottom:1px solid var(--line)}
.card .body{padding:.7rem .9rem 1rem}
.card .no{color:var(--accent);font-weight:700;margin-right:.4rem}
.card h2{font-size:1rem;margin:0 0 .3rem;line-height:1.45}
.card .who{color:var(--dim);font-size:.82rem;margin-bottom:.4rem}
.card .sum{color:var(--dim);font-size:.8rem;line-height:1.6;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.card .stat{margin-top:.6rem;color:var(--dim);font-size:.75rem;border-top:1px solid var(--line);padding-top:.5rem}
mark{background:var(--hit);color:#111}
footer{padding:0 1.6rem 2rem;color:var(--dim);font-size:.75rem}
"""

JS = r"""
var IDX = JSON.parse(document.getElementById('hub-index').textContent);
var LABEL = {takeaway:'\u91cd\u9ede', segment:'\u6bb5\u843d', bullet:'\u6458\u8981', quote:'\u539f\u8a71', slide:'\u6295\u5f71\u7247'};
var esc=function(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});};
var fmt=function(t){t=Math.max(0,Math.floor(t||0));
  return String(Math.floor(t/60)).padStart(2,'0')+':'+String(t%60).padStart(2,'0');};
var box=document.getElementById('results');
function run(qs){
  var terms=(qs||'').trim().toLowerCase().split(/\s+/).filter(Boolean);
  if(!terms.length){box.classList.remove('open');box.innerHTML='';return;}
  var hits=IDX.filter(function(r){return terms.every(function(t){return r.x.toLowerCase().indexOf(t)>=0;});}).slice(0,120);
  if(!hits.length){box.innerHTML='<div class="count">\u7121\u7b26\u5408\u7d50\u679c</div>';box.classList.add('open');return;}
  var perLecture={};
  hits.forEach(function(h){var k=h.no||h.v.split('.')[0];perLecture[k]=(perLecture[k]||0)+1;});
  var spread=Object.keys(perLecture).sort().map(function(n){return n+'('+perLecture[n]+')';}).join('\u3000');
  box.innerHTML='<div class="count">'+hits.length+' \u7b46\uff0c\u5206\u5e03\uff1a'+esc(spread)+'</div>'+hits.map(function(h){
    var pos=Math.max(0,h.x.toLowerCase().indexOf(terms[0])-24);
    var frag=esc((pos?'\u2026':'')+h.x.slice(pos,pos+120));
    terms.forEach(function(t){ frag=frag.replace(new RegExp('('+t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','ig'),'<mark>$1</mark>'); });
    return '<button class="sr" data-v="'+esc(h.v)+'" data-t="'+h.t+'"><span class="no">'+esc(h.no||h.v.split('.')[0])
      +'</span><span class="k">'+esc(LABEL[h.k]||h.k)+'</span><span>'+frag+'</span><span class="ts">'+fmt(h.t)+'</span></button>';
  }).join('');
  box.classList.add('open');
}
var tmr;
document.getElementById('q').addEventListener('input',function(e){clearTimeout(tmr);var v=e.target.value;tmr=setTimeout(function(){run(v);},120);});
document.addEventListener('click',function(e){
  var sr=e.target.closest('.sr');
  if(sr){ location.href=encodeURI(sr.dataset.v)+'?t='+sr.dataset.t; return; }
  if(!e.target.closest('.searchwrap')) box.classList.remove('open');
});
document.addEventListener('keydown',function(e){
  if(e.key==='/'&&!/^(INPUT|TEXTAREA)$/.test(e.target.tagName)){e.preventDefault();document.getElementById('q').focus();}
  if(e.key==='Escape') box.classList.remove('open');
});
"""

FOOTER_NOTE = "搜尋結果會直接開到該場的那個時間點。投影片文字為 OCR，有辨識誤差，僅供定位。"


def _json_block(element_id: str, payload: Any) -> str:
    """Embed JSON in a script tag without letting its text close the tag."""
    # ``<`` becomes its JSON escape, so the payload can neither open nor close a
    # tag no matter what a title or a slide's OCR text happens to contain.
    text = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return '<script id="%s" type="application/json">%s</script>' % (element_id, text)


def card_hrefs(card: Mapping[str, Any]) -> List[str]:
    """Every relative link one card emits, in the encoded form used in the page."""
    hrefs = [quote(str(card["viewer"]))]
    if card.get("thumb"):
        hrefs.append(quote(str(card["thumb"])))
    return hrefs


def render(
    cards: Sequence[Mapping[str, Any]],
    index: Sequence[Mapping[str, Any]],
    title: str,
    course: Optional[Mapping[str, Any]] = None,
) -> str:
    """The whole hub page as one string. Pure."""
    total_minutes = sum(int(card.get("minutes") or 0) for card in cards)
    total_frames = sum(len(card.get("frames") or []) for card in cards)
    total_segments = sum(len(card.get("segments") or []) for card in cards)

    card_html: List[str] = []
    for card in cards:
        thumb = ""
        if card.get("thumb"):
            thumb = '<img src="%s" loading="lazy" alt="">' % html.escape(
                quote(str(card["thumb"])), quote=True
            )
        card_html.append(
            '<a class="card" href="%s" data-stem="%s">%s<div class="body">'
            '<h2><span class="no">%s</span>%s</h2>'
            '<div class="who">%s</div><div class="sum">%s</div>'
            '<div class="stat">%d 分　·　%d 段　·　%d 張投影片</div></div></a>'
            % (
                html.escape(quote(str(card["viewer"])), quote=True),
                html.escape(str(card.get("stem") or ""), quote=True),
                thumb,
                html.escape(str(card.get("no") or "")),
                html.escape(str(card.get("topic") or "")),
                html.escape(str(card.get("speaker") or "")),
                html.escape(str(card.get("summary") or "")),
                int(card.get("minutes") or 0),
                len(card.get("segments") or []),
                len(card.get("frames") or []),
            )
        )

    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-Hant"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        "<title>" + html.escape(title) + "</title><style>" + CSS + "</style></head><body>\n"
        "<header>\n"
        "  <h1>" + html.escape(title) + "</h1>\n"
        '  <div class="meta">%d 場　·　共 %d 分（%.1f 小時）　·　%d 段　·　%d 張投影片</div>\n'
        % (len(cards), total_minutes, total_minutes / 60.0, total_segments, total_frames)
        + '  <div class="searchwrap">\n'
        '    <input id="q" placeholder="跨場搜尋：段落標題／重點／摘要／原話／投影片文字（按 / 聚焦）">\n'
        '    <div id="results"></div>\n'
        "  </div>\n"
        "</header>\n"
        + render_course(course or {})
        + "<main>" + "".join(card_html) + "</main>\n"
        "<footer>" + html.escape(FOOTER_NOTE) + "</footer>\n"
        + _json_block("hub-index", list(index)) + "\n"
        "<script>" + JS + "</script></body></html>\n"
    )


# --------------------------------------------------------------------------
# file-system layer
# --------------------------------------------------------------------------
def hub_path(folder: Path) -> Path:
    return Path(folder) / HUB_FILENAME


def preflight(folder: Path) -> List[PlannedFile]:
    """What ``l2n hub`` would write. Reads the filesystem, writes nothing."""
    return plan_for([hub_path(folder)])


def build(
    folder: Path, title: Optional[str] = None, out: Optional[Path] = None
) -> Dict[str, Any]:
    """Write the course hub and report its cards, index size and dead links."""
    folder = Path(folder)
    cards = collect(folder, require_viewer=False)
    index = build_index(cards)
    destination = Path(out) if out else hub_path(folder)
    page = render(cards, index, title or folder.name, load_course(folder))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8", newline="\n")
    hrefs: List[str] = []
    for card in cards:
        hrefs.extend(card_hrefs(card))
    return {
        "path": destination,
        "cards": cards,
        "index_rows": len(index),
        "hrefs": hrefs,
        "missing": missing_links(folder, hrefs),
    }


__all__ = [
    "CSS",
    "COURSE_FILE",
    "COURSE_QUESTION_LABEL",
    "COURSE_START_LABEL",
    "DERIVED_SUFFIXES",
    "HUB_FILENAME",
    "JS",
    "NO_COMMON_THREAD",
    "TITLES_FILE",
    "UNNUMBERED_SORT_KEY",
    "apply_titles",
    "build",
    "build_card",
    "build_index",
    "card_hrefs",
    "card_sort_key",
    "collect",
    "hub_path",
    "is_lecture_json",
    "course_lines",
    "load_course",
    "load_json",
    "load_titles",
    "missing_links",
    "parse_name",
    "preflight",
    "render",
    "render_course",
]
