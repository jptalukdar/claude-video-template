"""Small patch ops applied to spec.yaml (round-trip: comments/order kept), with undo history.

  add [e5|events|persist|intro|outro] k=v ...   new item (id optional for events -> auto)
  set <id|intro.N|outro.N|defaults|trim|out|spec> k=v ...   (dotted keys: frame.x=-0.1)
  unset <target> k ...
  del <id|intro.N|outro.N>
  mv <id> <index>                                reorder within its list (affects layer order)
"""
from __future__ import annotations

import shlex
import shutil
import time
from pathlib import Path

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .project import Project
from .spec import PREFIX
from .util import VtError, dump_yaml, load_yaml, parse_scalar

LISTS = ("events", "persist", "intro", "outro")
MAPS = ("defaults", "trim", "out")
MAX_HISTORY = 50


def _flow(m: dict) -> CommentedMap:
    cm = CommentedMap()
    for k, v in m.items():
        cm[k] = _flow(v) if isinstance(v, dict) else v
    cm.fa.set_flow_style()
    return cm


def _kv(tokens: list[str]) -> list[tuple[str, object]]:
    out = []
    for t in tokens:
        if "=" not in t:
            raise VtError(f"expected key=value, got '{t}'")
        k, v = t.split("=", 1)
        out.append((k.strip(), parse_scalar(v)))
    return out


def _setdot(m, key: str, val) -> None:
    parts = key.split(".")
    for p in parts[:-1]:
        if p not in m or not isinstance(m[p], dict):
            m[p] = _flow({})
        m = m[p]
    m[parts[-1]] = _flow(val) if isinstance(val, dict) else val


def _unsetdot(m, key: str) -> None:
    parts = key.split(".")
    for p in parts[:-1]:
        m = m.get(p) or {}
    if parts[-1] in m:
        del m[parts[-1]]


def _find(spec, target: str):
    """-> (list_name, index, item) for an id or intro.N/outro.N; or (None, None, mapping)."""
    if "." in target and target.split(".")[0] in LISTS and target.split(".")[1].isdigit():
        lst, i = target.split(".")[0], int(target.split(".")[1])
        seq = spec.get(lst) or []
        if i >= len(seq):
            raise VtError(f"{target}: index out of range ({len(seq)} items)")
        return lst, i, seq[i]
    for lst in LISTS:
        for i, it in enumerate(spec.get(lst) or []):
            if isinstance(it, dict) and str(it.get("id")) == target:
                return lst, i, it
    # implicit ids (e3 = third unnamed event) as shown by `vt check`
    for lst, pre in PREFIX.items():
        if target.startswith(pre) and target[len(pre):].isdigit():
            n = int(target[len(pre):])
            unnamed = [i for i, it in enumerate(spec.get(lst) or []) if isinstance(it, dict) and not it.get("id")]
            used = {str(it.get("id")) for it in spec.get(lst) or [] if isinstance(it, dict) and it.get("id")}
            k, idx = 0, None
            for i in unnamed:
                k += 1
                while f"{pre}{k}" in used:
                    k += 1
                if k == n:
                    idx = i
                    break
            if idx is not None:
                return lst, idx, spec[lst][idx]
    raise VtError(f"no item '{target}'")


def _next_id(spec, lst: str) -> str:
    pre = PREFIX[lst]
    used = {str(it.get("id")) for l2 in LISTS for it in spec.get(l2) or [] if isinstance(it, dict)}
    n = 1
    while f"{pre}{n}" in used:
        n += 1
    return f"{pre}{n}"


def apply_op(spec, tokens: list[str]) -> str:
    if not tokens:
        return ""
    op, rest = tokens[0].lower(), tokens[1:]
    if op == "add":
        if not rest:
            raise VtError("add needs a target")
        tgt = rest[0]
        if "=" in tgt:
            tgt, kv = "events", _kv(rest)
        else:
            kv = _kv(rest[1:])
        lst = tgt if tgt in LISTS else "events"
        item = dict(kv)
        if tgt not in LISTS:
            item = {"id": tgt, **{k: v for k, v in item.items() if k != "id"}}
        elif lst in ("events", "persist") and "id" not in item:
            item = {"id": _next_id(spec, lst), **item}
        if item.get("id") and any(isinstance(it, dict) and str(it.get("id")) == str(item["id"])
                                  for l2 in LISTS for it in spec.get(l2) or []):
            raise VtError(f"id '{item['id']}' exists (use set)")
        if spec.get(lst) is None:
            spec[lst] = CommentedSeq()
        nested = {}
        for k in list(item):
            if "." in k:
                nested[k] = item.pop(k)
        cm = _flow(item)
        for k, v in nested.items():
            _setdot(cm, k, v)
        spec[lst].append(cm)
        return str(item.get("id") or f"{lst}.{len(spec[lst]) - 1}")
    if op in ("set", "unset"):
        if not rest:
            raise VtError(f"{op} needs a target")
        tgt = rest[0]
        if tgt == "spec":
            m = spec
        elif tgt in MAPS:
            if spec.get(tgt) is None:
                spec[tgt] = _flow({})
            m = spec[tgt]
        else:
            m = _find(spec, tgt)[2]
        if op == "set":
            for k, v in _kv(rest[1:]):
                _setdot(m, k, v)
        else:
            for k in rest[1:]:
                _unsetdot(m, k)
        return tgt
    if op == "del":
        for tgt in rest:
            lst, i, _ = _find(spec, tgt)
            del spec[lst][i]
        return " ".join(rest)
    if op == "mv":
        if len(rest) != 2:
            raise VtError("mv <id> <index>")
        lst, i, it = _find(spec, rest[0])
        del spec[lst][i]
        spec[lst].insert(int(rest[1]), it)
        return rest[0]
    raise VtError(f"unknown op '{op}' (add|set|unset|del|mv)")


def split_ops(text: str) -> list[list[str]]:
    ops = []
    for line in text.replace(";", "\n").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ops.append(shlex.split(line, posix=True))
    return ops


def _hist(proj: Project) -> Path:
    d = proj.cache / "history"
    d.mkdir(exist_ok=True)
    return d


def patch(proj: Project, ops: list[list[str]]) -> list[str]:
    spec = load_yaml(proj.spec_path, rt=True)
    if spec is None:
        spec = CommentedMap()
    touched = [apply_op(spec, op) for op in ops]
    h = _hist(proj)
    if proj.spec_path.exists():
        shutil.copy2(proj.spec_path, h / f"{time.time_ns()}.yaml")
        old = sorted(h.glob("*.yaml"))
        for p in old[:-MAX_HISTORY]:
            p.unlink()
    dump_yaml(spec, proj.spec_path)
    _stamp_transcript(proj)
    return [t for t in touched if t]


def _stamp_transcript(proj: Project) -> None:
    """Remember which transcript the spec was last edited against (S-ids shift on re-transcription)."""
    from .util import read_json
    w = read_json(proj.cache / "words.json")
    if w:
        (proj.cache / "spec_tkey").write_text(w["key"], encoding="utf-8")


def undo(proj: Project) -> bool:
    hist = sorted(_hist(proj).glob("*.yaml"))
    if not hist:
        return False
    shutil.copy2(hist[-1], proj.spec_path)
    hist[-1].unlink()
    return True
