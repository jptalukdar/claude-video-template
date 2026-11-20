"""Animation presets -> ffmpeg expressions. Phases: in / out (entry, exit) and during (whole span)."""
from __future__ import annotations


def ease(p: str, kind: str | None) -> str:
    """Easing of a progress expression p in [0,1]."""
    k = (kind or "linear").lower()
    if k == "out":
        return f"(1-pow(1-({p}),2))"
    if k == "in":
        return f"pow({p},2)"
    if k == "inout":
        return f"if(lt({p},0.5),2*pow({p},2),1-pow(-2*({p})+2,2)/2)"
    if k == "back":
        return f"(1+2.70158*pow(({p})-1,3)+1.70158*pow(({p})-1,2))"
    return f"({p})"


def prog(t: str, start: float, d: float) -> str:
    if d <= 0:
        return f"gte({t},{start:.4f})"
    return f"clip(({t}-{start:.4f})/{d:.4f},0,1)"


def phases(anim: dict, D: float) -> tuple[dict, dict, dict]:
    i, o, du = dict(anim.get("in") or {}), dict(anim.get("out") or {}), dict(anim.get("during") or {})
    # never let entry+exit exceed the visible span
    di, do = float(i.get("d", 0) or 0), float(o.get("d", 0) or 0)
    if di + do > D and di + do > 0:
        k = D / (di + do)
        i["d"], o["d"] = di * k, do * k
    return i, o, du


def has_fade(ph: dict) -> bool:
    return ph.get("type") == "fade" or bool(ph.get("fade"))


def zoom_factor(i: dict, o: dict, D: float, t: str = "t") -> str | None:
    """Scale multiplier expression for in/out zoom phases (local time). None if no zoom."""
    parts = []
    if i.get("type") == "zoom":
        f0 = float(i.get("from", 0.6))
        e = ease(prog(t, 0, float(i["d"])), i.get("ease", "out"))
        parts.append(f"({f0}+(1-{f0})*{e})")
    if o.get("type") == "zoom":
        f1 = float(o.get("from", 0.6))
        d = float(o["d"])
        e = ease(prog(t, D - d, d), o.get("ease", "in"))
        parts.append(f"(1-(1-{f1})*{e})")
    return "*".join(parts) if parts else None


def zoom_peak(i: dict, o: dict) -> float:
    pk = 1.0
    for ph in (i, o):
        if ph.get("type") == "zoom" and (ph.get("ease") == "back"):
            pk = max(pk, 1.11)
    return pk


def slide_offsets(i: dict, o: dict, x0: int, y0: int, w: int, h: int, W: int, H: int, A: float, D: float,
                  t: str = "t") -> tuple[str, str]:
    """Additive x/y offset expressions (global time t, item starts at A) for slide phases."""
    xs, ys = [], []

    def vec(ph):
        frm = ph.get("from", "l")
        dist = ph.get("dist")
        if frm == "l":
            return (-(x0 + w) if dist is None else -float(dist) * W), 0
        if frm == "r":
            return ((W - x0) if dist is None else float(dist) * W), 0
        if frm == "u":
            return 0, (-(y0 + h) if dist is None else -float(dist) * H)
        return 0, ((H - y0) if dist is None else float(dist) * H)

    if i.get("type") == "slide":
        dx, dy = vec(i)
        e = ease(prog(t, A, float(i["d"])), i.get("ease", "out"))
        if dx:
            xs.append(f"{dx:.1f}*(1-{e})")
        if dy:
            ys.append(f"{dy:.1f}*(1-{e})")
    if o.get("type") == "slide":
        dx, dy = vec(o)
        d = float(o["d"])
        e = ease(prog(t, A + D - d, d), o.get("ease", "in"))
        if dx:
            xs.append(f"{dx:.1f}*{e}")
        if dy:
            ys.append(f"{dy:.1f}*{e}")
    return ("+".join(xs) or "0"), ("+".join(ys) or "0")


def fade_filters(i: dict, o: dict, D: float) -> list[str]:
    f = []
    if has_fade(i) and float(i.get("d", 0)) > 0:
        f.append(f"fade=t=in:st=0:d={float(i['d']):.3f}:alpha=1")
    if has_fade(o) and float(o.get("d", 0)) > 0:
        d = float(o["d"])
        f.append(f"fade=t=out:st={max(0.0, D - d):.3f}:d={d:.3f}:alpha=1")
    return f


def zoompan(du: dict, D: float, fps: float, w: int, h: int, fps_str: str) -> str | None:
    """'during' zoom/pan as a fixed-size crop-zoom (content moves, box stays)."""
    if not du:
        return None
    n = max(1, round(D * fps))
    if du.get("type") == "zoom":
        z0, z1 = float(du.get("from", 1.0)), float(du.get("to", 1.12))
        z = f"{z0}+({z1 - z0})*min(on/{n},1)"
        x, y = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    elif du.get("type") == "pan":
        dx, dy = float(du.get("dx", 0)), float(du.get("dy", 0))
        z = f"{1 + max(abs(dx), abs(dy)):.4f}"
        fx = f"(iw-iw/zoom)*({0 if dx >= 0 else 1}+({1 if dx >= 0 else -1})*min(on/{n},1))" if dx else "(iw-iw/zoom)/2"
        fy = f"(ih-ih/zoom)*({0 if dy >= 0 else 1}+({1 if dy >= 0 else -1})*min(on/{n},1))" if dy else "(ih-ih/zoom)/2"
        x, y = fx, fy
    else:
        return None
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={fps_str}"
