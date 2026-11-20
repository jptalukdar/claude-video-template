"""vt command line. Output is deliberately terse (one line per item) to keep Claude's token use low."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__
from .assets import add_asset, kind_of, list_assets
from .project import (PKG, Project, list_projects, projects_root, resolve_project, set_active)
from .util import VtError, dump_yaml, fmt_t, load_yaml, read_json, write_json


def P(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ helpers

def _proj(args) -> Project:
    return resolve_project(args.project, projects_root(args.root))


def _transcribe(proj: Project, model: str | None = None, lang: str | None = None, force: bool = False) -> str:
    from .spec import Presets, load_spec, merged_defaults
    from .transcribe import transcribe
    from .transcript import write_compressed
    spec = load_spec(proj)
    d = merged_defaults(spec, Presets.load(proj))
    src = proj.asset_path(spec["src"])
    data, cached = transcribe(src, proj.cache, model or d.get("model") or "small", lang or d.get("lang"),
                              force=force)
    n = write_compressed(proj.cache)
    return f"{'cached' if cached else 'done'} {len(data['words'])} words {n} sentences ({data['model']}, " \
           f"{data['lang']}, {data['device']})"


def _check(proj: Project, verbose: bool = True) -> tuple[list[str], int, int]:
    from .render.run import stamp
    from .timeline import build, summary
    tl = build(proj)
    e, w = len(tl.msgs.errors), len(tl.msgs.warnings)
    write_json(proj.cache / "check.json", {"e": e, "w": w, "stamp": stamp(proj, False)})
    resolved = {"total": tl.total, "main_start": tl.main_start, "head": tl.head, "tail": tl.tail,
                "segments": [[s.id, s.src, round(s.start, 3), round(s.dur, 3)] for s in tl.segments],
                "overlays": [[o.id, o.kind, round(o.out_a, 3), round(o.out_b, 3), o.x, o.y, o.w, o.h, o.layer]
                             for o in tl.overlays]}
    write_json(proj.cache / "resolved.json", resolved)
    return summary(tl, verbose), e, w


def _fresh_str(proj: Project, preview: bool) -> str:
    from .render.run import is_fresh
    f = is_fresh(proj, preview)
    return "none" if f is None else ("fresh" if f else "stale")


def _window(proj: Project, rng: str | None, around: str | None) -> tuple[float, float] | None:
    from .timeline import build
    from .transcript import Transcript, parse_range
    if not rng and not around:
        return None
    tl = build(proj, preview=True)
    if around:
        o = tl.find(around)
        if o is None:
            raise VtError(f"no item '{around}'")
        a, b = (o.out_a, o.out_b) if hasattr(o, "out_a") else (o.start, o.start + o.dur)
        return max(0.0, a - 1.0), min(tl.total, b + 1.0)
    r = parse_range(rng)
    tr = Transcript.load(proj.cache)
    a = tl.to_out(tr.sent(r[0])[2] - tl.head)
    b = tl.to_out(tr.sent(r[1])[3] - tl.head)
    return max(0.0, a - 0.3), min(tl.total, b + 0.3)


# ------------------------------------------------------------------ commands

def cmd_ls(args):
    root = projects_root(args.root)
    ps = list_projects(root)
    if not ps:
        P(f"no projects in {root} (vt new NAME VIDEO)")
        return 0
    act = (root / ".vt-active").read_text().strip() if (root / ".vt-active").exists() else ""
    w = max(len(p.name) for p in ps)
    for p in ps:
        spec = load_yaml(p.spec_path) or {}
        ev = len(spec.get("events") or [])
        trn = "tr:yes" if (p.dir / ".vt" / "words.json").exists() else "tr:no"
        ck = read_json(p.dir / ".vt" / "check.json")
        cks = "check:-" if not ck else ("check:ok" if not ck["e"] else f"check:{ck['e']}E") + \
            (f"/{ck['w']}W" if ck and ck["w"] else "")
        P(f"{'*' if p.name == act else ' '}{p.name:<{w}}  {trn}  {ev}ev  {cks}  preview:{_fresh_str(p, True)}  "
          f"final:{_fresh_str(p, False)}")
    return 0


def cmd_use(args):
    root = projects_root(args.root)
    p = resolve_project(args.name, root)
    set_active(root, p.name)
    P(f"active: {p.name}")
    return 0


def cmd_new(args):
    root = projects_root(args.root)
    d = root / args.name
    if d.exists() and any(d.iterdir()):
        raise VtError(f"{d} exists")
    d.mkdir(parents=True, exist_ok=True)
    proj = Project(d)
    shutil.copy2(PKG / "templates" / "project" / "spec.yaml", proj.spec_path)
    dump_yaml({}, proj.assets_path)
    add_asset(proj, Path(args.video), "main")
    n = 0
    if args.assets:
        for f in sorted(Path(args.assets).iterdir()):
            if f.is_file() and kind_of(f) != "other" and f.resolve() != Path(args.video).resolve():
                add_asset(proj, f)
                n += 1
    set_active(root, proj.name)
    from .probe import cached_probe
    pr = cached_probe(proj.asset_path("main"), proj.cache)
    v = pr["v"]
    P(f"created {proj.dir} (active) src {v['w']}x{v['h']}@{v['fps']} {pr['duration']:.2f}s +{n} assets")
    if not args.no_transcribe:
        P(_transcribe(proj, args.model, args.lang))
    return 0


def cmd_status(args):
    proj = resolve_project(args.name or args.project, projects_root(args.root))
    spec = load_yaml(proj.spec_path) or {}
    P(f"{proj.name}  {proj.dir}")
    pr = read_json(proj.cache / "probe.json")
    if pr:
        v, a = pr.get("v", {}), pr.get("a") or {}
        P(f"src {v.get('w')}x{v.get('h')}@{v.get('fps')} {pr['duration']:.2f}s {v.get('codec')}/{v.get('profile')} "
          f"{a.get('codec', '-')} {a.get('sample_rate', '')}")
    P("assets " + " | ".join(f"{k}: {' '.join(v)}" for k, v in sorted(list_assets(proj).items())))
    w = read_json(proj.cache / "words.json")
    s = read_json(proj.cache / "sentences.json")
    P(f"transcript {len(s['s']) if s else 0} sentences, {len(w['words']) if w else 0} words"
      + (f" ({w['model']}, {w['lang']})" if w else " — run vt transcribe"))
    P(f"spec {len(spec.get('events') or [])} events, {len(spec.get('persist') or [])} persist, "
      f"{len(spec.get('intro') or [])} intro, {len(spec.get('outro') or [])} outro")
    ck = read_json(proj.cache / "check.json")
    P(f"check {'-' if not ck else (str(ck['e']) + 'E ' + str(ck['w']) + 'W')}  preview {_fresh_str(proj, True)}  "
      f"final {_fresh_str(proj, False)}")
    return 0


def cmd_assets(args):
    proj = _proj(args)
    if args.add:
        for f in args.add:
            aid = add_asset(proj, Path(f), args.id if len(args.add) == 1 else None, copy=not args.link)
            P(f"{aid} = {proj.assets()[aid]}")
        return 0
    groups = list_assets(proj)
    for k in (["font"] if args.fonts else sorted(groups)):
        P(f"{k}: {' '.join(groups.get(k, [])) or '-'}")
    return 0


def cmd_presets(args):
    from .spec import Presets
    pr = Presets.load(_proj(args) if (args.project or _has_active(args)) else None)
    P("anim: " + " ".join(pr.anims))
    P("style: " + " ".join(pr.styles))
    P("pos: tl tc tr ml c mr bl bc br | 'x,y'   fit: none cover contain   tr: none fade dissolve slideleft "
      "slideright slideup slidedown wipeleft wiperight circleopen radial smoothleft ...")
    return 0


def _has_active(args) -> bool:
    try:
        _proj(args)
        return True
    except VtError:
        return False


def cmd_transcribe(args):
    from .batch import run_many, select
    root = projects_root(args.root)
    if args.all or args.glob or args.names:
        lines, failed = run_many(select(root, args.names, args.all, args.glob),
                                 lambda p: _transcribe(p, args.model, args.lang, args.force), 1, False,
                                 root / ".vt-batch.log")
        P("\n".join(lines))
        return 1 if failed else 0
    P(_transcribe(_proj(args), args.model, args.lang, args.force))
    return 0


def cmd_compress(args):
    from .transcript import write_compressed
    P(f"{write_compressed(_proj(args).cache, args.gap, args.max_words)} sentences")
    return 0


def cmd_transcript(args):
    from .transcript import Transcript, parse_range
    tr = Transcript.load(_proj(args).cache)
    r = parse_range(args.range) or (1, min(len(tr.sents), 40))
    P("\n".join(tr.lines(*r)))
    if not args.range and len(tr.sents) > 40:
        P(f"... {len(tr.sents)} sentences; use --range S41-S80 or vt find")
    return 0


def cmd_find(args):
    from .transcript import Transcript, parse_range
    tr = Transcript.load(_proj(args).cache)
    scope = parse_range(args.within)
    hits = tr.find(args.phrase, scope)
    fuzzy = False
    if not hits:
        hits, fuzzy = tr.find(args.phrase, scope, fuzzy=0.6, cross=0.5), True
    if not hits:
        P("no match")
        return 1
    for h in hits[:args.limit]:
        P(f"S{h.sid}#{h.occ} {fmt_t(h.start, 2)} {tr.context(h, latin=args.latin)}"
          + (f" ~{h.score:.2f}" if fuzzy else ""))
    if len(hits) > args.limit:
        P(f"... {len(hits)} hits")
    return 0


def cmd_patch(args):
    from .patch import patch, split_ops
    proj = _proj(args)
    if args.file:
        ops = split_ops(Path(args.file).read_text(encoding="utf-8"))
    elif args.ops and len(args.ops) == 1 and ("\n" in args.ops[0] or ";" in args.ops[0]):
        ops = split_ops(args.ops[0])
    elif args.ops:
        ops = split_ops(" ".join(_requote(t) for t in args.ops))
    else:
        ops = split_ops(sys.stdin.read())
    touched = set(" ".join(patch(proj, ops)).split())
    lines, e, w = _check(proj, verbose=True)
    P(lines[0])
    hidden = 0
    for ln in lines[1:]:
        if ln.startswith("E "):
            P(ln)
        elif ln.startswith("W "):
            if ln[2:].split(":")[0] in touched:
                P(ln)
            else:
                hidden += 1
        elif ln.split(" ")[0] in touched:
            P(ln)
    if hidden:
        P(f"({hidden} other warnings: vt check)")
    return 1 if e else 0


def _requote(t: str) -> str:
    if "=" in t:
        k, v = t.split("=", 1)
        if " " in v or "'" in v or '"' in v:
            return f"{k}=\"{v}\"" if '"' not in v else f"{k}='{v}'"
    return t


def cmd_undo(args):
    from .patch import undo
    proj = _proj(args)
    if not undo(proj):
        P("nothing to undo")
        return 1
    lines, e, _ = _check(proj, verbose=False)
    P("undone | " + "\n".join(lines))
    return 0


def cmd_check(args):
    from .batch import run_many, select
    root = projects_root(args.root)
    if args.all or args.glob or args.names:
        def fn(p):
            lines, e, w = _check(p, False)
            if e:
                raise VtError(f"{e}E {w}W: " + "; ".join(lines[1:3]))
            return f"ok {w}W"
        lines, failed = run_many(select(root, args.names, args.all, args.glob), fn, 1, False)
        P("\n".join(lines))
        return 1 if failed else 0
    lines, e, _ = _check(_proj(args), verbose=not args.quiet)
    P("\n".join(lines))
    return 1 if e else 0


def _render_one(p: Project, preview: bool, force: bool, window=None) -> str:
    from .render.run import render
    out, tl, diffs, secs = render(p, preview=preview, window=window, force=force)
    if secs == 0.0:
        return f"skip fresh {out.name}"
    rel = out.relative_to(p.dir).as_posix()
    span = f"{window[0]:.2f}-{window[1]:.2f}s of {tl.total:.2f}s" if window else f"{tl.total:.2f}s"
    msg = f"ok {rel} {span} {tl.prof.W}x{tl.prof.H}@{tl.prof.fps}"
    if diffs:
        msg += " | DIFF " + "; ".join(diffs)
    elif not preview and not window:
        msg += " | tech spec = source"
    return msg


def cmd_render(args, preview=False):
    from .batch import run_many, select
    root = projects_root(args.root)
    preview = preview or getattr(args, "preview", False)
    if getattr(args, "all", False) or getattr(args, "glob", None) or getattr(args, "names", None):
        projs = select(root, args.names, args.all, args.glob)
        if args.stale:
            from .render.run import is_fresh
            projs = [p for p in projs if not is_fresh(p, preview)]
        lines, failed = run_many(projs, lambda p: _render_one(p, preview, args.force), args.jobs, args.fail_fast,
                                 root / ".vt-batch.log")
        P("\n".join(lines))
        return 1 if failed else 0
    proj = _proj(args)
    win = _window(proj, getattr(args, "range", None), getattr(args, "around", None)) if preview else None
    P(_render_one(proj, preview, args.force or preview, win))
    return 0


def cmd_frame(args):
    from .render.run import frame
    from .timeline import build
    proj = _proj(args)
    tl = build(proj, preview=args.preview)
    try:
        t = float(args.target)
        name = f"t{t:.2f}"
    except ValueError:
        o = tl.find(args.target)
        if o is None:
            raise VtError(f"no item '{args.target}'")
        a, b = (o.out_a, o.out_b) if hasattr(o, "out_a") else (o.start, o.start + o.dur)
        t = {"start": a + 0.05, "end": b - 0.05}.get(args.at, (a + b) / 2)
        if args.at not in ("start", "end", "mid"):
            t = a + float(args.at)
        name = f"{args.target}_{args.at}"
    P(str(frame(proj, t, name, preview=args.preview)))
    return 0


# ------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="vt", description="spec-driven video editing")
    ap.add_argument("-p", "--project", help="project name or path (default: active)")
    ap.add_argument("--root", help="projects root (default: $VT_PROJECTS, vt.local.toml, ./projects)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ls", help="list projects")
    s.set_defaults(fn=cmd_ls)
    s = sub.add_parser("use", help="set active project")
    s.add_argument("name")
    s.set_defaults(fn=cmd_use)
    s = sub.add_parser("new", help="create a project from a video (+ assets dir), transcribe it")
    s.add_argument("name")
    s.add_argument("video")
    s.add_argument("--assets")
    s.add_argument("--no-transcribe", action="store_true")
    s.add_argument("--model")
    s.add_argument("--lang")
    s.set_defaults(fn=cmd_new)
    s = sub.add_parser("status", help="compact project summary")
    s.add_argument("name", nargs="?")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("assets", help="list or add assets")
    s.add_argument("--add", nargs="+", metavar="FILE")
    s.add_argument("--id")
    s.add_argument("--link", action="store_true", help="reference file in place instead of copying")
    s.add_argument("--fonts", action="store_true")
    s.set_defaults(fn=cmd_assets)
    s = sub.add_parser("presets", help="list animation/style preset names")
    s.set_defaults(fn=cmd_presets)

    def bulk(s):
        s.add_argument("names", nargs="*", help="project names (bulk)")
        s.add_argument("--all", action="store_true")
        s.add_argument("--glob")

    s = sub.add_parser("transcribe", help="speech-to-text with word timings (cached)")
    bulk(s)
    s.add_argument("--model")
    s.add_argument("--lang")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_transcribe)
    s = sub.add_parser("compress", help="rebuild sentence transcript")
    s.add_argument("--gap", type=float, default=0.7)
    s.add_argument("--max-words", type=int, default=25)
    s.set_defaults(fn=cmd_compress)
    s = sub.add_parser("transcript", help="print compressed transcript (range)")
    s.add_argument("--range", "-r")
    s.add_argument("--latin", action="store_true", help="romanise non-Latin words (fewer tokens)")
    s.set_defaults(fn=cmd_transcript)
    s = sub.add_parser("find", help="find a word/phrase -> sentence ids + times")
    s.add_argument("phrase")
    s.add_argument("--in", dest="within")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--latin", action="store_true", help="romanise non-Latin context")
    s.set_defaults(fn=cmd_find)
    s = sub.add_parser("patch", help="apply patch ops (args, -f file, or stdin)")
    s.add_argument("ops", nargs="*")
    s.add_argument("-f", "--file")
    s.set_defaults(fn=cmd_patch)
    s = sub.add_parser("undo", help="revert last patch")
    s.set_defaults(fn=cmd_undo)
    s = sub.add_parser("check", help="validate + resolve anchors")
    bulk(s)
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(fn=cmd_check)

    for name, pv in (("preview", True), ("render", False)):
        s = sub.add_parser(name, help=("low-res preview" if pv else "final render (tech spec = source)"))
        bulk(s)
        if pv:
            s.add_argument("--range", "-r", help="S10-S14: only this part")
            s.add_argument("--around", help="item id: only around it")
        else:
            s.add_argument("--preview", action="store_true")
        s.add_argument("--stale", action="store_true", help="bulk: only projects whose output is stale")
        s.add_argument("--force", action="store_true", help="re-render even if fresh")
        s.add_argument("-j", "--jobs", type=int, default=1)
        s.add_argument("--fail-fast", action="store_true")
        s.set_defaults(fn=(lambda a, _pv=pv: cmd_render(a, _pv)))

    s = sub.add_parser("frame", help="export one frame (item id or output seconds)")
    s.add_argument("target")
    s.add_argument("--at", default="mid", help="start | mid | end | seconds into item")
    s.add_argument("--preview", action="store_true")
    s.set_defaults(fn=cmd_frame)
    return ap


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except VtError as e:
        P(f"error: {e}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
