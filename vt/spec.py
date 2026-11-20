"""Spec loading, preset merging and schema validation (terse, id-prefixed messages)."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .project import PKG, Project
from .util import VtError, load_yaml

TOP_KEYS = {"src", "offset", "trim", "out", "defaults", "intro", "outro", "persist", "events"}
ITEM_KEYS = {"id", "at", "to", "dur", "off", "text", "sub", "img", "clip", "style", "font", "anim", "pos", "w",
             "h", "scale", "fit", "op", "layer", "slot", "stack", "frame", "tr", "scope", "audio", "vol",
             "color", "size"}
CARD_KEYS = {"id", "img", "clip", "text", "style", "font", "dur", "anim", "tr", "trd", "fit", "audio", "bg",
             "color", "size", "vol"}
FRAME_KEYS = {"x", "y", "scale", "bg"}
TRIM_KEYS = {"head", "tail"}
OUT_KEYS = {"match", "crf", "fps", "size", "vbr", "abr", "preset", "vcodec", "two_pass", "name"}
POS_GRID = {"tl", "tc", "tr", "ml", "c", "mr", "bl", "bc", "br"}
FITS = {"none", "cover", "contain"}


@dataclass
class Presets:
    defaults: dict
    anims: dict
    styles: dict

    @classmethod
    def load(cls, proj: Project | None = None) -> "Presets":
        out = []
        for name in ("defaults", "animations", "styles"):
            d = dict(load_yaml(PKG / "presets" / f"{name}.yaml") or {})
            if proj and (proj.presets_dir / f"{name}.yaml").exists():
                d.update(load_yaml(proj.presets_dir / f"{name}.yaml") or {})
            out.append(d)
        return cls(*out)


@dataclass
class Msgs:
    items: list = field(default_factory=list)

    def err(self, who: str, msg: str):
        self.items.append(("E", who, msg))

    def warn(self, who: str, msg: str):
        self.items.append(("W", who, msg))

    @property
    def errors(self):
        return [m for m in self.items if m[0] == "E"]

    @property
    def warnings(self):
        return [m for m in self.items if m[0] == "W"]

    def lines(self) -> list[str]:
        return [f"{k} {who}: {msg}" for k, who, msg in self.items]


def load_spec(proj: Project) -> dict:
    d = load_yaml(proj.spec_path)
    if d is None:
        raise VtError(f"no spec.yaml in {proj.dir}")
    if not isinstance(d, dict):
        raise VtError("spec.yaml must be a mapping")
    return d


def item_lists(spec: dict) -> dict[str, list]:
    return {k: list(spec.get(k) or []) for k in ("intro", "outro", "persist", "events")}


PREFIX = {"events": "e", "persist": "p", "intro": "i", "outro": "o"}


def assign_ids(spec: dict) -> None:
    """Give every list item an id (in memory only) — e1.., p1.., i1.., o1.."""
    for lst, pre in PREFIX.items():
        used = {str(it.get("id")) for it in spec.get(lst) or [] if isinstance(it, dict) and it.get("id")}
        n = 0
        for it in spec.get(lst) or []:
            if isinstance(it, dict) and not it.get("id"):
                n += 1
                while f"{pre}{n}" in used:
                    n += 1
                it["id"] = f"{pre}{n}"
                used.add(it["id"])


def merged_defaults(spec: dict, pre: Presets) -> dict:
    d = copy.deepcopy(pre.defaults)
    d.update(spec.get("defaults") or {})
    return d


def validate(spec: dict, pre: Presets, assets: dict[str, str], msgs: Msgs) -> None:
    from .assets import kind_of

    for k in spec:
        if k not in TOP_KEYS:
            msgs.err("spec", f"unknown key '{k}'")
    src = spec.get("src")
    if not src:
        msgs.err("spec", "missing src (asset id of the source video)")
    elif src not in assets:
        msgs.err("spec", f"src '{src}' not in assets")
    for k in (spec.get("trim") or {}):
        if k not in TRIM_KEYS:
            msgs.err("trim", f"unknown key '{k}'")
    for k in (spec.get("out") or {}):
        if k not in OUT_KEYS:
            msgs.err("out", f"unknown key '{k}'")
    ids: dict[str, str] = {}

    def chk_asset(who, key, val, kind):
        if val is None:
            return
        if val not in assets:
            msgs.err(who, f"{key} '{val}' not in assets")
        elif kind_of(assets[val]) != kind:
            msgs.err(who, f"{key} '{val}' is not a{'n' if kind[0] in 'aeiou' else ''} {kind}")

    def chk_common(who, it):
        if it.get("anim") is not None and it["anim"] not in pre.anims:
            msgs.err(who, f"unknown anim '{it['anim']}' (vt presets)")
        if it.get("style") is not None and it["style"] not in pre.styles:
            msgs.err(who, f"unknown style '{it['style']}' (vt presets)")
        f = it.get("font")
        if f is not None and f in assets and kind_of(assets[f]) != "font":
            msgs.err(who, f"font '{f}' is not a font asset")
        chk_asset(who, "img", it.get("img"), "img")
        chk_asset(who, "clip", it.get("clip"), "video")
        if it.get("fit") is not None and it["fit"] not in FITS:
            msgs.err(who, f"fit must be one of {sorted(FITS)}")

    for lst in ("intro", "outro"):
        for it in spec.get(lst) or []:
            who = str(it.get("id", lst)) if isinstance(it, dict) else lst
            if not isinstance(it, dict):
                msgs.err(lst, "items must be mappings")
                continue
            for k in it:
                if k not in CARD_KEYS:
                    msgs.err(who, f"unknown key '{k}' for {lst} card")
            if sum(1 for k in ("img", "clip", "text") if it.get(k) is not None) != 1:
                msgs.err(who, "card needs exactly one of img | clip | text")
            chk_common(who, it)
            if who in ids:
                msgs.err(who, f"duplicate id (also in {ids[who]})")
            ids[who] = lst

    for lst in ("persist", "events"):
        for it in spec.get(lst) or []:
            if not isinstance(it, dict):
                msgs.err(lst, "items must be mappings")
                continue
            who = str(it.get("id"))
            if who in ids:
                msgs.err(who, f"duplicate id (also in {ids[who]})")
            ids[who] = lst
            for k in it:
                if k not in ITEM_KEYS:
                    msgs.err(who, f"unknown key '{k}'")
            kinds = [k for k in ("text", "img", "clip", "frame") if it.get(k) is not None]
            if len(kinds) != 1:
                msgs.err(who, "needs exactly one of text | img | clip | frame")
            if it.get("sub") is not None and it.get("text") is None:
                msgs.err(who, "sub needs text")
            if lst == "events" and it.get("at") is None:
                msgs.err(who, "missing at")
            fr = it.get("frame")
            if fr is not None:
                if not isinstance(fr, dict):
                    msgs.err(who, "frame must be a mapping {x, y, scale, bg}")
                else:
                    for k in fr:
                        if k not in FRAME_KEYS:
                            msgs.err(who, f"unknown frame key '{k}'")
                    bg = fr.get("bg")
                    if bg is not None and not str(bg).startswith("#"):
                        chk_asset(who, "frame.bg", bg, "img")
            pos = it.get("pos")
            if pos is not None and str(pos) not in POS_GRID:
                try:
                    a, b = (float(x) for x in str(pos).split(","))
                except ValueError:
                    msgs.err(who, f"bad pos '{pos}' (tl..br or 'x,y')")
            if it.get("scope") not in (None, "main", "all"):
                msgs.err(who, "scope must be main | all")
            chk_common(who, it)
