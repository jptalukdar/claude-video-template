"""Resolve a spec into a concrete timeline: segments (intro/main/outro), overlays with pixel boxes,
main-time and output-time intervals, layers and auto-stacking. Everything the renderer needs."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .anchors import Resolver, parse_anchor
from .assets import kind_of
from .probe import cached_probe, probe
from .project import Project
from .render.profile import Profile, build_profile
from .render.textimg import missing_glyphs, render_text, resolve_font, text_card
from .spec import Msgs, Presets, assign_ids, load_spec, merged_defaults, validate
from .transcript import Transcript
from .util import VtError, file_fingerprint, fmt_t, read_json, run, need_tool, write_json

GRID = {"tl": (0, 0), "tc": (1, 0), "tr": (2, 0), "ml": (0, 1), "c": (1, 1), "mr": (2, 1),
        "bl": (0, 2), "bc": (1, 2), "br": (2, 2)}


@dataclass
class Segment:
    id: str
    kind: str                 # card | main
    src: str                  # img | clip | text | main
    path: Path
    dur: float
    tr: str = "none"          # transition from this segment into the next
    trd: float = 0.0
    anim: dict = field(default_factory=dict)
    fit: str = "contain"
    audio: str = "silence"    # card clip: clip | silence
    vol: float = 1.0
    bg: str = "#000000"
    start: float = 0.0        # output time
    clip_in: float = 0.0      # main: source in-point


@dataclass
class Overlay:
    id: str
    lst: str                  # events | persist
    kind: str                 # text | img | clip | frame
    path: Path | None
    a: float                  # main time
    b: float
    out_a: float = 0.0
    out_b: float = 0.0
    w: int = 0
    h: int = 0
    x: int = 0
    y: int = 0
    fit: str = "none"
    nat: tuple = (0, 0)       # native size (img/clip)
    anim: dict = field(default_factory=dict)
    anim_name: str = "none"
    op: float = 1.0
    layer: int = 10
    order: int = 0
    pos: str = "bc"
    slot: str | None = None
    scope: str = "main"
    frame: dict = field(default_factory=dict)
    tr: float = 0.4
    vol: float = 0.0
    audio: str = "mute"
    stack_off: int = 0


@dataclass
class Timeline:
    prof: Profile
    probe: dict
    src_path: Path
    head: float
    tail: float
    src_dur: float
    main_dur: float
    segments: list
    overlays: list
    total: float
    main_start: float
    msgs: Msgs
    defaults: dict

    def to_out(self, m: float) -> float:
        return self.main_start + m

    def find(self, oid: str):
        for o in self.overlays:
            if o.id == oid:
                return o
        for s in self.segments:
            if s.id == oid:
                return s
        return None


# ---------------------------------------------------------------- helpers

def _asset_info(proj: Project, aid: str) -> tuple[Path, str]:
    p = proj.asset_path(aid)
    if not p.exists():
        raise VtError(f"asset '{aid}' file missing: {p}")
    return p, kind_of(p)


def _media_dur(path: Path, cache: Path) -> tuple[float, tuple[int, int], bool]:
    db = read_json(cache / "media.json", {})
    fp = file_fingerprint(path)
    if fp not in db:
        pr = probe(path)
        v = pr.get("v") or {}
        db[fp] = [pr["duration"], [v.get("w") or 0, v.get("h") or 0], bool(pr.get("a"))]
        write_json(cache / "media.json", db)
    d, wh, has_a = db[fp]
    return float(d), tuple(wh), bool(has_a)


def _freeze(path: Path, cache: Path, at_end: bool, dur: float) -> float:
    """Length of a static (frozen) run at the start or end of the video (0 if none)."""
    key = f"{file_fingerprint(path)}:{'end' if at_end else 'start'}"
    db = read_json(cache / "freeze.json", {})
    if key in db:
        return db[key]
    win = min(15.0, dur)
    cmd = [need_tool("ffmpeg"), "-hide_banner", "-nostats"]
    if at_end:
        cmd += ["-ss", f"{max(0.0, dur - win):.3f}"]
    cmd += ["-t", f"{win:.3f}", "-i", str(path), "-vf", "freezedetect=n=0.003:d=0.4", "-an", "-f", "null", "-"]
    err = run(cmd, check=False).stderr or ""
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", err)]
    val = 0.0
    if not at_end and starts and starts[0] < 0.05:
        val = ends[0] if ends else win
    if at_end and starts and len(ends) < len(starts):  # last freeze runs to the end
        val = win - starts[-1]
    db[key] = round(val, 3)
    write_json(cache / "freeze.json", db)
    return db[key]


def _anim(pre: Presets, name: str | None, who: str, msgs: Msgs) -> dict:
    name = name or "none"
    if name not in pre.anims:
        msgs.err(who, f"unknown anim '{name}'")
        return {}
    return copy.deepcopy(pre.anims[name] or {})


def _style(pre: Presets, it: dict, d: dict) -> dict:
    st = copy.deepcopy(pre.styles.get(it.get("style") or d.get("style") or "caption", {}))
    for k in ("color", "size"):
        if it.get(k) is not None:
            st[k] = it[k]
    return st


def _font(name: str, proj: Project, assets: dict, who: str, msgs: Msgs) -> tuple[Path, int] | None:
    try:
        return resolve_font(name, assets, proj.dir)
    except VtError as e:
        msgs.err(who, str(e))
        return None


# ---------------------------------------------------------------- main entry

def build(proj: Project, preview: bool = False, need_transcript: bool = True) -> Timeline:
    pre = Presets.load(proj)
    spec = load_spec(proj)
    assign_ids(spec)
    assets = proj.assets()
    msgs = Msgs()
    validate(spec, pre, assets, msgs)
    if msgs.errors and any(w == "spec" for _, w, _ in msgs.errors):
        raise VtError("; ".join(msgs.lines()))
    d = merged_defaults(spec, pre)

    src_path, _ = _asset_info(proj, spec["src"])
    pr = cached_probe(src_path, proj.cache)
    prof = build_profile(pr, spec.get("out"), preview=preview)
    W, H = prof.W, prof.H
    src_dur = pr.get("v", {}).get("duration") or pr["duration"]
    if pr.get("a"):
        src_dur = min(src_dur, pr["duration"])

    tr = None
    try:
        tr = Transcript.load(proj.cache)
    except VtError:
        if need_transcript:
            pass  # anchors that need it will report errors
    offset = float(spec.get("offset") or 0)

    # ---- trim
    trim = spec.get("trim") or {}
    r0 = Resolver(tr, offset, (0.0, src_dur))
    head = trim.get("head", 0) or 0
    tail = trim.get("tail", 0) or 0
    if head == "auto":
        head = _freeze(src_path, proj.cache, False, src_dur)
    elif isinstance(head, str):
        head = r0.resolve(head, "start", "trim.head", msgs) or 0.0
    if tail == "auto":
        tail = _freeze(src_path, proj.cache, True, src_dur)
    elif isinstance(tail, str):  # anchor = cut point
        cut = r0.resolve(tail, "end", "trim.tail", msgs)
        tail = (src_dur - cut) if cut is not None else 0.0
    head, tail = float(head), float(tail)
    main_dur = src_dur - head - tail
    if main_dur <= 0.1:
        raise VtError(f"trim leaves no video (head {head:.2f} + tail {tail:.2f} >= {src_dur:.2f})")
    bounds = (head, head + main_dur)

    # ---- overlays: resolve times (source time), lazily for cross references
    items = [("persist", it) for it in spec.get("persist") or []] + [("events", it) for it in spec.get("events") or []]
    by_id = {str(it["id"]): (lst, it) for lst, it in items if isinstance(it, dict)}
    times: dict[str, tuple[float | None, float | None]] = {}
    visiting: set[str] = set()

    def ref(oid: str, edge: str) -> float:
        if oid in seg_times:
            a, b = seg_times[oid]
            return a if edge == "start" else b
        if oid not in by_id:
            raise VtError(f"unknown item '{oid}'")
        a, b = resolve_item(oid)
        if a is None:
            raise VtError(f"'{oid}' unresolved")
        if edge == "end" and b is None:
            raise VtError(f"'{oid}.end' depends on to: next")
        return a if edge == "start" else b

    seg_times: dict[str, tuple[float, float]] = {}

    def safe_ref(oid, edge):
        try:
            return ref(oid, edge)
        except VtError as e:
            raise _RefErr(str(e))

    res = Resolver(tr, offset, bounds, ref=safe_ref, known=set(by_id))

    def resolve_item(oid: str):
        if oid in times:
            return times[oid]
        if oid in visiting:
            raise VtError(f"circular reference via '{oid}'")
        visiting.add(oid)
        lst, it = by_id[oid]
        who = oid
        try:
            at = it.get("at")
            a = bounds[0] if at is None else res.resolve(at, "start", who, msgs)
            b = None
            to = it.get("to")
            if a is not None:
                a += float(it.get("off") or 0)
                if to is not None and str(to).strip() == "next":
                    b = None
                elif to is not None:
                    b = res.resolve(to, "end", who, msgs)
                    if b is not None:
                        b += float(it.get("off") or 0)
                elif it.get("dur") is not None:
                    b = a + float(it["dur"])
                elif lst == "persist" or it.get("frame") is not None:
                    b = bounds[1]
                else:
                    b = a + float(d["dur"])
        except _RefErr as e:
            msgs.err(who, str(e))
            a = b = None
        visiting.discard(oid)
        times[oid] = (a, b)
        return a, b

    for oid in by_id:
        try:
            resolve_item(oid)
        except VtError as e:
            msgs.err(oid, str(e))
            times[oid] = (None, None)

    # ---- build overlay objects
    ovs: list[Overlay] = []
    for order, (lst, it) in enumerate(items):
        if not isinstance(it, dict):
            continue
        oid = str(it["id"])
        a, b = times.get(oid, (None, None))
        if a is None:
            continue
        kind = next(k for k in ("text", "img", "clip", "frame") if it.get(k) is not None) \
            if any(it.get(k) is not None for k in ("text", "img", "clip", "frame")) else None
        if kind is None:
            continue
        o = Overlay(id=oid, lst=lst, kind=kind, path=None, a=a - head, b=(b - head) if b is not None else None,
                    order=order, layer=int(it.get("layer", 100 if lst == "persist" else (0 if kind == "frame" else 10))),
                    pos=str(it.get("pos") or d["pos"]), op=float(it.get("op", d["op"])),
                    fit=str(it.get("fit") or ("none" if kind == "text" else d["fit"])),
                    slot=str(it["slot"]) if it.get("slot") is not None else None,
                    scope=str(it.get("scope") or "main"), tr=float(it.get("tr") or d["frame_tr"]))
        o._to_next = it.get("to") is not None and str(it.get("to")).strip() == "next"  # type: ignore[attr-defined]
        o._has_dur = it.get("dur") is not None  # type: ignore[attr-defined]
        o._dur = float(it.get("dur") or d["dur"])  # type: ignore[attr-defined]
        o._it = it  # type: ignore[attr-defined]
        o.anim_name = str(it.get("anim") or ("none" if kind == "frame" else d["anim"]))
        o.anim = _anim(pre, o.anim_name, oid, msgs)
        ovs.append(o)

    # ---- to: next  (group by slot, else by pos)
    groups: dict[str, list[Overlay]] = {}
    for o in ovs:
        if o.kind != "frame":
            groups.setdefault(o.slot or f"pos:{o.pos}", []).append(o)
    for g in groups.values():
        g.sort(key=lambda o: (o.a, o.order))
        for i, o in enumerate(g):
            if not getattr(o, "_to_next", False):
                continue
            nxt = next((n for n in g[i + 1:] if n.a > o.a + 1e-3), None)
            if nxt is None:
                o.b = o.a + o._dur if o._has_dur else main_dur  # type: ignore[attr-defined]
            else:
                ov = 0.0
                oo, ni = (o.anim.get("out") or {}), (nxt.anim.get("in") or {})
                if oo.get("type") == "fade" and ni.get("type") == "fade":
                    ov = min(float(oo.get("d", 0)), float(ni.get("d", 0)))
                o.b = nxt.a + ov
    for o in ovs:
        if o.b is None:
            o.b = main_dur

    # ---- clamp to main segment
    kept = []
    for o in ovs:
        if o.b <= o.a:
            msgs.err(o.id, f"ends before it starts ({fmt_t(o.a + head, 2)} -> {fmt_t(o.b + head, 2)} src)")
            continue
        if o.b <= 0 or o.a >= main_dur:
            msgs.warn(o.id, "outside the kept video (trimmed) — dropped")
            continue
        if o.a < -0.01 or o.b > main_dur + 0.01:
            msgs.warn(o.id, "clipped to the kept video")
        o.a, o.b = max(0.0, o.a), min(main_dur, o.b)
        kept.append(o)
    ovs = kept

    # ---- media + sizes
    for o in ovs:
        it = o._it  # type: ignore[attr-defined]
        try:
            if o.kind == "text":
                st = _style(pre, it, d)
                fm = _font(str(it.get("font") or st.get("font") or d["font"]), proj, assets, o.id, msgs)
                fs = _font(str((st.get("sub") or {}).get("font") or it.get("font") or st.get("font") or d["font"]),
                           proj, assets, o.id, msgs)
                if not fm or not fs:
                    continue
                miss = missing_glyphs(str(it["text"]), *fm)
                if miss:
                    msgs.warn(o.id, f"font lacks glyphs: {miss}")
                o.path = render_text(str(it["text"]), it.get("sub"), st, fm, fs, W, H, proj.cache)
                with Image.open(o.path) as im:
                    o.w, o.h = im.size
            elif o.kind in ("img", "clip"):
                p, _ = _asset_info(proj, it[o.kind])
                o.path = p
                if o.kind == "img":
                    with Image.open(p) as im:
                        o.nat = im.size
                else:
                    dur, wh, has_a = _media_dur(p, proj.cache)
                    o.nat = wh
                    o.audio = str(it.get("audio") or "mute")
                    o.vol = float(it.get("vol", 1.0))
                    if o.b - o.a > dur + 0.05:
                        msgs.warn(o.id, f"clip is only {dur:.2f}s; holds last frame")
                o.w, o.h = _box(o, it, d, W, H)
            elif o.kind == "frame":
                fr = dict(it["frame"])
                bg = fr.get("bg")
                if bg is not None and not str(bg).startswith("#"):
                    fr["bg_path"] = _asset_info(proj, bg)[0]
                o.frame = fr
        except VtError as e:
            msgs.err(o.id, str(e))
    ovs = [o for o in ovs if o.kind == "frame" or o.path is not None]

    _layout(ovs, d, W, H)

    # ---- segments
    segs: list[Segment] = []
    for lst in ("intro", "outro"):
        if lst == "outro":
            segs.append(Segment("main", "main", "main", src_path, main_dur, clip_in=head))
        for it in spec.get(lst) or []:
            if not isinstance(it, dict):
                continue
            sid = str(it["id"])
            try:
                if it.get("img") is not None:
                    p, _ = _asset_info(proj, it["img"])
                    sg = Segment(sid, "card", "img", p, float(it.get("dur") or d["card_dur"]))
                elif it.get("clip") is not None:
                    p, _ = _asset_info(proj, it["clip"])
                    cd, _, has_a = _media_dur(p, proj.cache)
                    sg = Segment(sid, "card", "clip", p, min(float(it.get("dur") or cd), cd))
                    sg.audio = str(it.get("audio") or ("clip" if has_a else "silence"))
                    if sg.audio == "clip" and not has_a:
                        sg.audio = "silence"
                else:
                    st = _style(pre, {**it, "style": it.get("style") or "card"}, d)
                    fn = _font(str(it.get("font") or st.get("font") or d["font"]), proj, assets, sid, msgs)
                    if not fn:
                        continue
                    p = text_card(str(it["text"]), st, fn, W, H, str(it.get("bg") or d["card_bg"]), proj.cache)
                    sg = Segment(sid, "card", "text", p, float(it.get("dur") or d["card_dur"]))
            except VtError as e:
                msgs.err(sid, str(e))
                continue
            sg.tr = str(it.get("tr") or d["tr"])
            sg.trd = float(it.get("trd") or d["trd"]) if sg.tr != "none" else 0.0
            sg.fit = str(it.get("fit") or "contain")
            sg.bg = str(it.get("bg") or d["card_bg"])
            sg.vol = float(it.get("vol", 1.0))
            sg.anim = _anim(pre, it.get("anim") or "none", sid, msgs)
            sg._lst = lst  # type: ignore[attr-defined]
            segs.append(sg)
    # transitions between consecutive segments: intro card -> its tr; into outro card -> its tr
    t = 0.0
    for i, sg in enumerate(segs):
        if i > 0:
            prev = segs[i - 1]
            if getattr(prev, "_lst", None) == "intro":
                kind, dd = prev.tr, prev.trd
            elif getattr(sg, "_lst", None) == "outro":
                kind, dd = sg.tr, sg.trd
            else:
                kind, dd = "none", 0.0
            dd = min(dd, prev.dur / 2, sg.dur / 2) if kind != "none" else 0.0
            prev._out_tr = (kind, dd)  # type: ignore[attr-defined]
            t -= dd
        sg.start = t
        t += sg.dur
    total = t
    main_seg = next(s for s in segs if s.kind == "main")
    for sg in segs:
        seg_times[sg.id] = (sg.start, sg.start + sg.dur)
    for o in ovs:
        o.out_a, o.out_b = main_seg.start + o.a, main_seg.start + o.b

    return Timeline(prof, pr, src_path, head, tail, src_dur, main_dur, segs, ovs, total, main_seg.start, msgs, d)


class _RefErr(Exception):
    pass


def _box(o: Overlay, it: dict, d: dict, W: int, H: int) -> tuple[int, int]:
    nw, nh = o.nat
    if not nw or not nh:
        raise VtError("cannot read media size")
    if o.fit in ("cover", "contain"):
        return W, H
    if it.get("w") is not None:
        w = float(it["w"]) * W
        h = float(it["h"]) * H if it.get("h") is not None else w * nh / nw
    elif it.get("h") is not None:
        h = float(it["h"]) * H
        w = h * nw / nh
    elif it.get("scale") is not None:
        w, h = nw * float(it["scale"]), nh * float(it["scale"])
    else:
        w = float(d["img_w"]) * W
        h = w * nh / nw
    w, h = max(2, round(w)), max(2, round(h))
    return w + w % 2, h + h % 2


def _layout(ovs: list[Overlay], d: dict, W: int, H: int) -> None:
    m = round(float(d["margin"]) * H)
    gap = round(float(d["gap"]) * H)
    for o in ovs:
        if o.kind == "frame":
            continue
        if o.fit in ("cover", "contain"):
            o.x, o.y = 0, 0
            continue
        if o.pos in GRID:
            gx, gy = GRID[o.pos]
            o.x = [m, (W - o.w) // 2, W - o.w - m][gx]
            o.y = [m, (H - o.h) // 2, H - o.h - m][gy]
        else:
            fx, fy = (float(v) for v in o.pos.split(","))
            o.x, o.y = round(fx * W - o.w / 2), round(fy * H - o.h / 2)
    # auto-stack: same grid pos, overlapping time, different slot
    if str(d.get("stack", "auto")) != "auto":
        return
    placed: list[Overlay] = []
    for o in sorted([o for o in ovs if o.kind != "frame" and o.fit == "none" and o.pos in GRID],
                    key=lambda o: (o.a, o.order)):
        if str(o._it.get("stack", "auto")) == "none":  # type: ignore[attr-defined]
            continue
        off = 0
        for p in placed:
            if p.pos == o.pos and p.a < o.b and o.a < p.b and (o.slot is None or p.slot != o.slot):
                off = max(off, p.stack_off + p.h + gap)
        o.stack_off = off
        if off:
            gy = GRID[o.pos][1]
            o.y = o.y - off if gy == 2 else o.y + off
        placed.append(o)


def summary(tl: Timeline, verbose: bool = True) -> list[str]:
    p = tl.prof
    e, w = len(tl.msgs.errors), len(tl.msgs.warnings)
    head = (f"{'ok' if not e else 'FAIL'} {len(tl.overlays)} overlays {len(tl.segments) - 1} cards | {e}E {w}W | "
            f"out {tl.total:.2f}s {p.W}x{p.H}@{p.fps} main {fmt_t(tl.head, 2)}-{fmt_t(tl.head + tl.main_dur, 2)} src")
    lines = [head] + tl.msgs.lines()
    if verbose:
        for sg in tl.segments:
            if sg.kind == "card":
                trn = getattr(sg, "_out_tr", None)
                lines.append(f"{sg.id} {sg.start:.2f}-{sg.start + sg.dur:.2f} card {sg.src}"
                             + (f" tr {trn[0]} {trn[1]:.2f}" if trn and trn[0] != "none" else ""))
        for o in sorted(tl.overlays, key=lambda o: (o.out_a, o.order)):
            what = o.kind if o.kind != "frame" else "frame"
            extra = f" {o.pos}" if o.kind != "frame" else ""
            if o.stack_off:
                extra += f" stack+{o.stack_off}"
            lines.append(f"{o.id} {o.out_a:.2f}-{o.out_b:.2f} L{o.layer} {what}{extra} {o.anim_name}")
    return lines
