"""Compile a Timeline into ffmpeg inputs + one filter_complex script."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..timeline import Overlay, Segment, Timeline
from ..util import short_hash
from . import anim as A


@dataclass
class Graph:
    inputs: list = field(default_factory=list)   # list of arg lists
    chains: list = field(default_factory=list)
    n: int = 0
    _lbl: int = 0

    def add_input(self, args: list[str]) -> int:
        self.inputs.append(args)
        self.n += 1
        return self.n - 1

    def label(self, p: str = "l") -> str:
        self._lbl += 1
        return f"{p}{self._lbl}"

    def chain(self, s: str):
        self.chains.append(s)

    def script(self) -> str:
        return ";\n".join(self.chains)

    def input_args(self) -> list[str]:
        out = []
        for a in self.inputs:
            out += a
        return out


def _color(c: str) -> str:
    c = str(c).lstrip("#")
    return "0x" + c[:6] + ("@" + f"{int(c[6:8], 16) / 255:.3f}" if len(c) == 8 else "")


def _yuv(tl: Timeline, alpha: bool) -> str:
    p = tl.prof
    rng = "tv" if p.range_tv else "pc"
    return f"scale=out_color_matrix={p.matrix}:out_range={rng},format={'yuva420p' if alpha else 'yuv420p'}"


def _prescale(path: Path, w: int, h: int, mode: str, cache: Path, bg: str | None = None) -> Path:
    """Cache a 2x-resolution fitted copy of an image for jitter-free zoompan.
    mode: cover (fill+crop) | contain (fit on `bg` canvas) | stretch (box already matches aspect)."""
    key = short_hash(str(path), path.stat().st_mtime, w, h, mode, bg)
    out = cache / "pre" / f"{key}.png"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as im:
        im = im.convert("RGBA")
        tw, th = w * 2, h * 2
        if mode == "cover":
            k = max(tw / im.width, th / im.height)
            im = im.resize((max(tw, round(im.width * k)), max(th, round(im.height * k))), Image.LANCZOS)
            l, t = (im.width - tw) // 2, (im.height - th) // 2
            im = im.crop((l, t, l + tw, t + th))
        elif mode == "contain":
            k = min(tw / im.width, th / im.height)
            im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
            c = (bg or "#00000000").lstrip("#")
            c = c + "FF" if len(c) == 6 else c
            canvas = Image.new("RGBA", (tw, th), tuple(int(c[i:i + 2], 16) for i in (0, 2, 4, 6)))
            canvas.alpha_composite(im, ((tw - im.width) // 2, (th - im.height) // 2))
            im = canvas
        else:
            im = im.resize((tw, th), Image.LANCZOS)
        im.save(out)
    return out


# ------------------------------------------------------------------ segments

def _card(g: Graph, tl: Timeline, sg: Segment, cache: Path) -> tuple[str, str]:
    p = tl.prof
    W, H, fps = p.W, p.H, p.fps
    D = sg.dur
    i_in, o_ph, du = A.phases(sg.anim, D)
    v, a = g.label("cv"), g.label("ca")
    if sg.src in ("img", "text"):
        if du:
            src = _prescale(sg.path, W, H, "cover" if sg.fit == "cover" else "contain", cache, sg.bg)
            idx = g.add_input(["-loop", "1", "-framerate", fps, "-t", f"{D:.3f}", "-i", str(src)])
            f = [f"[{idx}:v]format=rgba", A.zoompan(du, D, p.fps_f, W, H, fps)]
        else:
            idx = g.add_input(["-loop", "1", "-framerate", fps, "-t", f"{D:.3f}", "-i", str(sg.path)])
            f = [f"[{idx}:v]format=rgba"] + _fit(sg.fit, W, H, sg.bg)
        if sg.fit != "cover":
            f.append(f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={_color(sg.bg)}")
        chain_a = None
    else:  # clip
        idx = g.add_input(["-t", f"{D:.3f}", "-i", str(sg.path)])
        f = [f"[{idx}:v]fps={fps},format=rgba"] + _fit(sg.fit, W, H, sg.bg)
        if sg.fit != "cover":
            f.append(f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={_color(sg.bg)}")
        chain_a = f"[{idx}:a]" if sg.audio == "clip" else None
    # card-level fades go through black
    if A.has_fade(i_in) and float(i_in.get("d", 0)) > 0:
        f.append(f"fade=t=in:st=0:d={float(i_in['d']):.3f}")
    if A.has_fade(o_ph) and float(o_ph.get("d", 0)) > 0:
        f.append(f"fade=t=out:st={D - float(o_ph['d']):.3f}:d={float(o_ph['d']):.3f}")
    f += [f"fps={fps}", "setsar=1", _yuv(tl, False), f"trim=duration={D:.4f}", "setpts=PTS-STARTPTS", "settb=AVTB"]
    g.chain(",".join(f) + f"[{v}]")
    g.chain(_audio_chain(tl, chain_a, D, sg.vol) + f"[{a}]")
    return v, a


def _fit(fit: str, W: int, H: int, bg: str) -> list[str]:
    if fit == "cover":
        return [f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={W}:{H}"]
    return [f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos"]


def _audio_chain(tl: Timeline, src: str | None, D: float, vol: float = 1.0) -> str:
    p = tl.prof
    fmt = f"aresample={p.sr},aformat=sample_fmts=fltp:sample_rates={p.sr}:channel_layouts={p.layout}"
    if src is None:
        return f"anullsrc=r={p.sr}:cl={p.layout},atrim=duration={D:.4f},{fmt},asetpts=PTS-STARTPTS"
    v = f",volume={vol}" if vol != 1.0 else ""
    return f"{src}{fmt}{v},apad,atrim=duration={D:.4f},asetpts=PTS-STARTPTS"


def _main(g: Graph, tl: Timeline) -> tuple[str, str]:
    p = tl.prof
    W, H, fps = p.W, p.H, p.fps
    v = g.label("mv")
    g.chain(f"[0:v]trim=start={tl.head:.4f}:duration={tl.main_dur:.4f},setpts=PTS-STARTPTS,fps={fps},"
            f"scale={W}:{H}:flags=bicubic,setsar=1,format=yuv420p[{v}]")
    a = g.label("ma")
    if tl.probe.get("a"):
        g.chain(f"[0:a]atrim=start={tl.head:.4f}:duration={tl.main_dur:.4f},asetpts=PTS-STARTPTS,"
                + _audio_chain(tl, "", tl.main_dur).lstrip(",") + f"[{a}]")
    else:
        g.chain(_audio_chain(tl, None, tl.main_dur) + f"[{a}]")
    return v, a


# ------------------------------------------------------------------ frame (base video move)

def _frame(g: Graph, tl: Timeline, base: str, o: Overlay, cache: Path) -> str:
    p = tl.prof
    W, H, fps = p.W, p.H, p.fps
    a, b = o.a, o.b
    D = b - a
    fr = o.frame
    s = float(fr.get("scale", 1.0))
    T = min(o.tr, D / 2)
    k = f"min({A.ease(A.prog('t', a, T), 'inout')},{A.ease(f'clip(({b:.4f}-t)/{T:.4f},0,1)', 'inout')})" \
        if T > 0 else "1"
    b0, b1, bg, mv, fr_l, out = (g.label(x) for x in ("fa", "fb", "fbg", "fmv", "ffr", "fo"))
    g.chain(f"[{base}]split[{b0}][{b1}]")
    bgv = fr.get("bg") or "#000000"
    if fr.get("bg_path"):
        idx = g.add_input(["-loop", "1", "-framerate", fps, "-t", f"{D:.3f}", "-i", str(fr["bg_path"])])
        g.chain(f"[{idx}:v]format=rgba,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
                f"{_yuv(tl, False)},fps={fps},setpts=PTS-STARTPTS+{a:.4f}/TB[{bg}]")
    else:
        g.chain(f"color=c={_color(str(bgv))}:s={W}x{H}:r={fps}:d={D:.4f},format=yuv420p,"
                f"setpts=PTS-STARTPTS+{a:.4f}/TB[{bg}]")
    sc = f"(1+({s - 1:.4f})*{k})" if s != 1 else "1"
    if s != 1:
        g.chain(f"[{b1}]trim=start={a:.4f}:end={b:.4f},scale=w='trunc({W}*{sc}/2)*2':h='trunc({H}*{sc}/2)*2':"
                f"eval=frame[{mv}]")
    else:
        g.chain(f"[{b1}]trim=start={a:.4f}:end={b:.4f}[{mv}]")
    dx, dy = float(fr.get("x", 0)) * W, float(fr.get("y", 0)) * H
    x = f"(main_w-overlay_w)/2+{dx:.1f}*{k}"
    y = f"(main_h-overlay_h)/2+{dy:.1f}*{k}"
    g.chain(f"[{bg}][{mv}]overlay=x='{x}':y='{y}':eval=frame:shortest=1[{fr_l}]")
    g.chain(f"[{b0}][{fr_l}]overlay=0:0:eof_action=pass[{out}]")
    return out


# ------------------------------------------------------------------ overlays

def _overlay(g: Graph, tl: Timeline, base: str, o: Overlay, use_out_time: bool, cache: Path,
             audio_taps: list) -> str:
    p = tl.prof
    W, H, fps = p.W, p.H, p.fps
    Aa = o.out_a if use_out_time else o.a
    D = o.b - o.a
    i_ph, o_ph, du = A.phases(o.anim, D)
    w, h = o.w, o.h
    cover = o.fit == "cover"
    f: list[str] = []
    if o.kind == "clip":
        idx = g.add_input(["-t", f"{D:.3f}", "-i", str(o.path)])
        f.append(f"[{idx}:v]fps={fps},format=rgba")
        if o.audio == "mix":
            audio_taps.append((idx, Aa, D, o.vol))
        if du:
            f.append(f"scale={w * 2}:{h * 2}" + (":force_original_aspect_ratio=increase" if cover else ""))
            if cover:
                f.append(f"crop={w * 2}:{h * 2}")
            f.append(A.zoompan(du, D, p.fps_f, w, h, fps))
        else:
            f += ([f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={W}:{H}"] if cover
                  else [f"scale={w}:{h}:flags=lanczos"])
    else:
        if du:
            src = _prescale(o.path, w, h, "cover" if cover else ("contain" if o.fit == "contain" else "stretch"),
                            cache)
            idx = g.add_input(["-loop", "1", "-framerate", fps, "-t", f"{D:.3f}", "-i", str(src)])
            f += [f"[{idx}:v]format=rgba", A.zoompan(du, D, p.fps_f, w, h, fps)]
        else:
            idx = g.add_input(["-loop", "1", "-framerate", fps, "-t", f"{D:.3f}", "-i", str(o.path)])
            f.append(f"[{idx}:v]format=rgba")
            if o.kind == "img":
                f += ([f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={W}:{H}"]
                      if cover else ([f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos",
                                      f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0"]
                                     if o.fit == "contain" else [f"scale={w}:{h}:flags=lanczos"]))
    # in/out zoom on the whole box -> constant canvas
    zf = A.zoom_factor(i_ph, o_ph, D)
    cw, ch = w, h
    if zf:
        pk = A.zoom_peak(i_ph, o_ph)
        cw, ch = int(w * pk + 3) // 2 * 2, int(h * pk + 3) // 2 * 2
        f.append(f"scale=w='max(2,trunc({w}*{zf}/2)*2)':h='max(2,trunc({h}*{zf}/2)*2)':eval=frame")
        f.append(f"pad=w={cw}:h={ch}:x='(ow-iw)/2':y='(oh-ih)/2':color=black@0:eval=frame")
    f += A.fade_filters(i_ph, o_ph, D)
    if o.op < 1:
        f.append(f"colorchannelmixer=aa={o.op:.3f}")
    f += [_yuv(tl, True), f"setpts=PTS-STARTPTS+{Aa:.4f}/TB"]
    ov = g.label("ov")
    g.chain(",".join(f) + f"[{ov}]")
    x0, y0 = o.x - (cw - w) // 2, o.y - (ch - h) // 2
    sx, sy = A.slide_offsets(i_ph, o_ph, o.x, o.y, w, h, W, H, Aa, D)
    out = g.label("b")
    g.chain(f"[{base}][{ov}]overlay=x='{x0}+{sx}':y='{y0}+{sy}':eval=frame:eof_action=pass:format=auto[{out}]")
    return out


# ------------------------------------------------------------------ whole graph

def build_graph(tl: Timeline, cache: Path, src_args: list[str] | None = None) -> tuple[Graph, str, str]:
    g = Graph()
    g.add_input(src_args or ["-i", str(tl.src_path)])
    mv, ma = _main(g, tl)
    # base transforms first, then overlays by layer
    for o in sorted([o for o in tl.overlays if o.kind == "frame"], key=lambda o: (o.a, o.order)):
        mv = _frame(g, tl, mv, o, cache)
    taps: list = []
    main_ovs = sorted([o for o in tl.overlays if o.kind != "frame" and o.scope != "all"],
                      key=lambda o: (o.layer, o.order))
    for o in main_ovs:
        mv = _overlay(g, tl, mv, o, False, cache, taps)
    if taps:
        labels = [ma]
        for idx, a0, D, vol in taps:
            lb = g.label("oa")
            ms = int(a0 * 1000)
            g.chain(_audio_chain(tl, f"[{idx}:a]", D, vol) + f",adelay={ms}|{ms}[{lb}]")
            labels.append(lb)
        nm = g.label("ma")
        g.chain("".join(f"[{x}]" for x in labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0[{nm}]")
        ma = nm
    # main segment ends: unify timebase for xfade
    v2 = g.label("mv")
    g.chain(f"[{mv}]settb=AVTB[{v2}]")
    mv = v2
    # segments
    parts = []
    for sg in tl.segments:
        if sg.kind == "main":
            parts.append((sg, mv, ma))
        else:
            v, a = _card(g, tl, sg, cache)
            parts.append((sg, v, a))
    cur_sg, cv, ca = parts[0]
    cur_dur = cur_sg.dur
    for sg, v, a in parts[1:]:
        kind, dd = getattr(cur_sg, "_out_tr", ("none", 0.0))
        nv, na = g.label("xv"), g.label("xa")
        if kind == "none" or dd <= 0:
            g.chain(f"[{cv}][{ca}][{v}][{a}]concat=n=2:v=1:a=1[{nv}][{na}]")
            cur_dur += sg.dur
        else:
            g.chain(f"[{cv}][{v}]xfade=transition={kind}:duration={dd:.3f}:offset={cur_dur - dd:.4f}[{nv}]")
            g.chain(f"[{ca}][{a}]acrossfade=d={dd:.3f}[{na}]")
            cur_dur += sg.dur - dd
        cv, ca, cur_sg = nv, na, sg
    for o in sorted([o for o in tl.overlays if o.kind != "frame" and o.scope == "all"],
                    key=lambda o: (o.layer, o.order)):
        cv = _overlay(g, tl, cv, o, True, cache, [])
    p = tl.prof
    # tag exactly like the source; untagged sources stay untagged
    params = []
    for k, v in (("range", p.color_range), ("color_primaries", p.color_primaries),
                 ("color_trc", p.color_transfer), ("colorspace", p.color_space)):
        params.append(f"{k}={v if v and v != 'unknown' else 'unknown'}")
    fv = g.label("vout")
    g.chain(f"[{cv}]format={p.pix_fmt},setparams={':'.join(params)}[{fv}]")
    return g, fv, ca
