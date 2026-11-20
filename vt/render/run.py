"""Run ffmpeg for previews, final renders, windows and single frames; render freshness stamps."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from .. import __version__
from ..probe import diff_spec, probe
from ..project import PKG, Project
from ..timeline import Timeline, build
from ..util import VtError, need_tool, run
from .graph import build_graph


def stamp(proj: Project, preview: bool) -> str:
    """Hash of everything that affects the output: spec, manifest + asset files, presets, tool code."""
    h = hashlib.sha1()
    h.update(f"{__version__}|{preview}".encode())
    for p in [proj.spec_path, proj.assets_path] + sorted(proj.presets_dir.glob("*.yaml")) \
            if proj.presets_dir.exists() else [proj.spec_path, proj.assets_path]:
        if p.exists():
            h.update(p.read_bytes())
    for aid, rel in sorted(proj.assets().items()):
        ap = proj.asset_path(aid)
        if ap.exists():
            st = ap.stat()
            h.update(f"{aid}|{st.st_size}|{int(st.st_mtime)}".encode())
    for p in sorted(PKG.rglob("*")):
        if p.suffix in (".py", ".yaml") and "__pycache__" not in p.parts:
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def out_path(proj: Project, preview: bool, tl_name: str | None = None) -> Path:
    if preview:
        return proj.out / "preview.mp4"
    return proj.out / f"{tl_name or 'final'}.mp4"


def is_fresh(proj: Project, preview: bool) -> bool | None:
    """True fresh, False stale, None never rendered."""
    from ..spec import load_spec
    try:
        name = (load_spec(proj).get("out") or {}).get("name")
    except VtError:
        return None
    op = out_path(proj, preview, name)
    sp = op.with_suffix(".stamp")
    if not op.exists() or not sp.exists():
        return None
    return sp.read_text().strip() == stamp(proj, preview)


def _ffmpeg(args: list[str], log: Path) -> None:
    cmd = [need_tool("ffmpeg"), "-hide_banner", "-y", "-nostdin", "-loglevel", "error"] + args
    cp = run(cmd, check=False)
    log.write_text(" ".join(f'"{a}"' if " " in a else a for a in cmd) + "\n\n" + (cp.stderr or ""), encoding="utf-8")
    if cp.returncode != 0:
        tail = [ln for ln in (cp.stderr or "").strip().splitlines() if ln.strip()][-4:]
        raise VtError("ffmpeg failed: " + " | ".join(tail) + f" (log {log})")


def render(proj: Project, preview: bool = False, window: tuple[float, float] | None = None,
           force: bool = True, tl: Timeline | None = None) -> tuple[Path, Timeline, list[str], float]:
    """Render; returns (path, timeline, tech-spec diffs, seconds). window = output-time (a, b)."""
    t0 = time.time()
    tl = tl or build(proj, preview=preview)
    if tl.msgs.errors:
        raise VtError(f"{len(tl.msgs.errors)} errors — run vt check")
    from ..spec import load_spec
    name = (load_spec(proj).get("out") or {}).get("name")
    op = out_path(proj, preview, name)
    st = stamp(proj, preview)
    if not force and window is None and op.exists() and op.with_suffix(".stamp").exists() \
            and op.with_suffix(".stamp").read_text().strip() == st:
        return op, tl, [], 0.0
    g, vout, aout = build_graph(tl, proj.cache)
    gs = proj.cache / ("graph_preview.txt" if preview else "graph.txt")
    gs.write_text(g.script(), encoding="utf-8")
    args = g.input_args() + ["-/filter_complex", str(gs), "-map", f"[{vout}]", "-map", f"[{aout}]"]
    if window:
        a, b = window
        args += ["-ss", f"{max(0.0, a):.3f}", "-t", f"{b - a:.3f}"]
        op = proj.out / "preview_window.mp4"
    args += tl.prof.encode_args()
    tmp = op.with_name(op.stem + ".part.mp4")
    if tl.prof.two_pass and tl.prof.vbr and not preview and not window:
        # two-pass ABR lands on the source bitrate; pass 1 is video-only
        null = "NUL" if os.name == "nt" else "/dev/null"
        plog = str(proj.cache / "x264pass")
        a1 = g.input_args() + ["-/filter_complex", str(gs), "-map", f"[{vout}]", "-map", f"[{aout}]"] \
            + tl.prof.encode_args()
        _ffmpeg(a1 + ["-pass", "1", "-passlogfile", plog, "-f", "mp4", null], proj.cache / "ffmpeg.log")
        _ffmpeg(args + ["-pass", "2", "-passlogfile", plog, str(tmp)], proj.cache / "ffmpeg.log")
        for f in proj.cache.glob("x264pass*"):
            f.unlink(missing_ok=True)
    else:
        _ffmpeg(args + [str(tmp)], proj.cache / "ffmpeg.log")
    os.replace(tmp, op)
    diffs = []
    if not preview and not window:
        op.with_suffix(".stamp").write_text(st)
        diffs = diff_spec(tl.probe, probe(op)) if not (load_spec(proj).get("out") or {}) else []
    elif preview and not window:
        op.with_suffix(".stamp").write_text(st)
    return op, tl, diffs, time.time() - t0


def frame(proj: Project, t_out: float, name: str, preview: bool = False) -> Path:
    tl = build(proj, preview=preview)
    if tl.msgs.errors:
        raise VtError(f"{len(tl.msgs.errors)} errors — run vt check")
    g, vout, aout = build_graph(tl, proj.cache)
    g.chain(f"[{aout}]anullsink")
    gs = proj.cache / "graph_frame.txt"
    gs.write_text(g.script(), encoding="utf-8")
    d = proj.out / "frames"
    d.mkdir(exist_ok=True)
    op = d / f"{name}.png"
    _ffmpeg(g.input_args() + ["-/filter_complex", str(gs), "-map", f"[{vout}]", "-ss", f"{t_out:.3f}",
                              "-frames:v", "1", "-update", "1", str(op)], proj.cache / "ffmpeg.log")
    return op
