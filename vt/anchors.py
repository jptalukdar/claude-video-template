"""Anchor grammar -> source-video time.

  12.5 | '@1:23.4'          absolute source time (no drift offset applied)
  S12 | S12.end            sentence start / end
  S12:word | S12:'two words' | S12:word#2     phrase inside a sentence (#n = nth occurrence there)
  word | 'two words'#3     phrase searched in the whole transcript (#n = nth occurrence overall)
  e1.start | e1.end         another item's resolved time
  start | end              main segment bounds (after trim)
  any of the above + '+0.5' / '-0.2'
`at` uses a phrase's first-word start, `to` its last-word end.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .spec import Msgs
from .transcript import Transcript
from .util import VtError, parse_t

OFF_RE = re.compile(r"\s*([+-]\s*\d+(?:\.\d+)?)$")
SENT_RE = re.compile(r"^S(\d+)(?:\.(start|end))?$", re.I)
SPHRASE_RE = re.compile(r"^S(\d+):(.+)$", re.I | re.S)
REF_RE = re.compile(r"^([A-Za-z_][\w-]*)\.(start|end)$")
OCC_RE = re.compile(r"^(.*?)#(\d+)$", re.S)


@dataclass
class Anchor:
    kind: str                 # abs | sent | phrase | ref | kw | next
    t: float = 0.0            # abs
    sid: int | None = None    # sent / phrase scope
    phrase: str = ""
    occ: int | None = None
    ref: str = ""
    edge: str | None = None   # explicit .start/.end
    off: float = 0.0
    raw: str = ""


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    return s


def parse_anchor(a) -> Anchor:
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        return Anchor("abs", t=float(a), raw=str(a))
    s = str(a).strip()
    raw = s
    off = 0.0
    m = OFF_RE.search(s)
    if m:
        head = s[: m.start()].strip()
        # a trailing +/-number is an offset when it follows a complete anchor token
        # (quote phrases that themselves end in a number, e.g. 'covid-19')
        if head and (head[-1] in "'\"" or SENT_RE.match(head) or REF_RE.match(head) or OCC_RE.match(head)
                     or head in ("start", "end", "next") or head.startswith("@") or SPHRASE_RE.match(head)
                     or re.search(r"\.(start|end)$", head)
                     or re.fullmatch(r"[^\W\d_][\w']*", head)):
            off = float(m.group(1).replace(" ", ""))
            s = head
    if s.startswith("@"):
        try:
            return Anchor("abs", t=parse_t(s[1:]), off=off, raw=raw)
        except ValueError:
            raise VtError(f"bad absolute anchor '{raw}'")
    if s in ("start", "end"):
        return Anchor("kw", edge=s, off=off, raw=raw)
    if s == "next":
        return Anchor("next", off=off, raw=raw)
    m = SENT_RE.match(s)
    if m:
        return Anchor("sent", sid=int(m.group(1)), edge=(m.group(2) or None), off=off, raw=raw)
    m = SPHRASE_RE.match(s)
    if m:
        sid, rest = int(m.group(1)), m.group(2)
        rest, edge = _edge(rest)
        occ = None
        mo = OCC_RE.match(rest)
        if mo:
            rest, occ = mo.group(1), int(mo.group(2))
        return Anchor("phrase", sid=sid, phrase=_unquote(rest), occ=occ, edge=edge, off=off, raw=raw)
    m = REF_RE.match(s)
    if m:  # item reference — or a bare word with .start/.end (decided at resolve time)
        return Anchor("ref", ref=m.group(1), edge=m.group(2), off=off, raw=raw)
    s, edge = _edge(s)
    occ = None
    mo = OCC_RE.match(s)
    if mo:
        s, occ = mo.group(1), int(mo.group(2))
    return Anchor("phrase", phrase=_unquote(s), occ=occ, edge=edge, off=off, raw=raw)


def _edge(s: str) -> tuple[str, str | None]:
    m = re.match(r"^(.*)\.(start|end)$", s, re.S)
    return (m.group(1), m.group(2)) if m else (s, None)


class Resolver:
    """Resolves anchors to source time. `ref(id, edge)` resolves another item's time (source time)."""

    def __init__(self, tr: Transcript | None, offset: float, bounds: tuple[float, float],
                 ref: Callable[[str, str], float] | None = None, fuzzy: float = 0.75, cross: float = 0.5,
                 known: set | None = None):
        self.tr, self.offset, self.bounds, self.ref, self.fuzzy = tr, offset, bounds, ref, fuzzy
        self.cross, self.known = cross, known or set()

    def resolve(self, a, edge: str, who: str, msgs: Msgs) -> float | None:
        try:
            an = parse_anchor(a)
        except VtError as e:
            msgs.err(who, str(e))
            return None
        t = self._base(an, edge, who, msgs)
        return None if t is None else t + an.off

    def _need_tr(self, who, msgs) -> bool:
        if self.tr is None:
            msgs.err(who, "anchor needs a transcript (vt transcribe)")
            return False
        return True

    def _base(self, an: Anchor, edge: str, who: str, msgs: Msgs) -> float | None:
        if an.kind == "abs":
            return an.t
        if an.kind == "kw":
            return self.bounds[0] if an.edge == "start" else self.bounds[1]
        if an.kind == "next":
            msgs.err(who, "'next' is only valid in `to:`")
            return None
        if an.kind == "ref" and an.ref not in self.known:
            an = Anchor("phrase", phrase=an.ref, edge=an.edge, off=an.off, raw=an.raw)  # bare word.start/.end
        if an.kind == "ref":
            if not self.ref:
                msgs.err(who, f"cannot reference '{an.ref}' here")
                return None
            return self.ref(an.ref, an.edge)
        if not self._need_tr(who, msgs):
            return None
        tr = self.tr
        if an.kind == "sent":
            try:
                s = tr.sent(an.sid)
            except VtError as e:
                msgs.err(who, str(e))
                return None
            e = an.edge or "start"
            return (s[2] if e == "start" else s[3]) + self.offset
        # phrase
        scope = (an.sid, an.sid) if an.sid else None
        try:
            hits = tr.find(an.phrase, scope)
        except VtError as e:
            msgs.err(who, f"{e} in '{an.raw}'")
            return None
        where = f"S{an.sid}" if an.sid else "transcript"
        if not hits:
            fz = tr.find(an.phrase, scope, fuzzy=self.fuzzy, cross=self.cross)
            if not fz:
                near = tr.find(an.phrase, None, fuzzy=0.5)[:3]
                sug = ", ".join(f"S{h.sid}:'{' '.join(w[0] for w in tr.words[h.i0:h.i1 + 1])}'" for h in near)
                msgs.err(who, f"'{an.phrase}' not found in {where}" + (f"; nearest: {sug}" if sug else ""))
                return None
            got = " ".join(w[0] for w in tr.words[fz[0].i0:fz[0].i1 + 1])
            msgs.warn(who, f"~'{got}' S{fz[0].sid} ({fz[0].score:.2f})")
            hits = sorted(fz, key=lambda h: h.i0) if an.occ else fz[:1]
        if an.occ:
            if an.occ > len(hits):
                msgs.err(who, f"'{an.phrase}' only occurs {len(hits)}x in {where}")
                return None
            h = hits[an.occ - 1]
        else:
            h = hits[0]
            if len(hits) > 1:
                if an.sid:
                    msgs.warn(who, f"'{an.phrase}' x{len(hits)} in S{an.sid}, used #1 (add #n)")
                else:
                    sids = ",".join(f"S{x.sid}" for x in hits[:6])
                    msgs.warn(who, f"'{an.phrase}' x{len(hits)} ({sids}), used S{h.sid} (scope with S<n>: or #n)")
        return (h.start if (an.edge or edge) == "start" else h.end) + self.offset
