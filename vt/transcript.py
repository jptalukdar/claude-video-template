"""Compressed transcript (sentences with IDs) and phrase search over word timings.

Claude only ever sees `transcript.txt` ranges and `find` results; word timings stay local.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .translit import is_latin, skeleton, to_latin
from .util import VtError, fmt_t, norm_word, read_json, write_json

END_PUNCT = re.compile(r"[.?!…।॥？！。]['\")\]]*$")  # incl. Devanagari danda, CJK stops


def compress(words: list, gap: float = 0.7, max_words: int = 25) -> list[list]:
    """Split words into sentences -> [[i0, i1, start, end], ...] (i1 exclusive)."""
    sents, i0 = [], 0
    n = len(words)
    for i, w in enumerate(words):
        last = i == n - 1
        brk = last or END_PUNCT.search(w[0]) or (words[i + 1][1] - w[2] > gap) or (i + 1 - i0 >= max_words)
        if brk:
            sents.append([i0, i + 1, words[i0][1], w[2]])
            i0 = i + 1
    return sents


def write_compressed(cache: Path, gap: float = 0.7, max_words: int = 25) -> int:
    d = read_json(cache / "words.json")
    if not d:
        raise VtError("no transcript yet: run `vt transcribe`")
    words = d["words"]
    sents = compress(words, gap, max_words)
    write_json(cache / "sentences.json", {"key": d["key"], "s": sents})
    lines = [f"S{k + 1} {fmt_t(s[2])} " + " ".join(w[0] for w in words[s[0]:s[1]]) for k, s in enumerate(sents)]
    (cache / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(sents)


@dataclass
class Hit:
    sid: int          # 1-based sentence id
    occ: int          # occurrence number of this phrase within the sentence (1-based)
    i0: int           # first word index
    i1: int           # last word index (inclusive)
    start: float
    end: float
    score: float = 1.0


@dataclass
class Transcript:
    words: list
    sents: list
    norm: list = field(default_factory=list)
    sid_of: list = field(default_factory=list)

    def __post_init__(self):
        self.norm = [norm_word(w[0]) for w in self.words]
        self.lat = [re.sub(r"[^a-z0-9]", "", to_latin(w[0])) for w in self.words]
        self.native = [is_latin(w[0]) for w in self.words]
        self.sid_of = [0] * len(self.words)
        for k, s in enumerate(self.sents):
            for i in range(s[0], s[1]):
                self.sid_of[i] = k + 1

    @classmethod
    def load(cls, cache: Path) -> "Transcript":
        d = read_json(cache / "words.json")
        s = read_json(cache / "sentences.json")
        if not d:
            raise VtError("no transcript yet: run `vt transcribe`")
        if not s or s.get("key") != d["key"]:
            write_compressed(cache)
            s = read_json(cache / "sentences.json")
        return cls(d["words"], s["s"])

    # --- sentences ---
    def sent(self, sid: int) -> list:
        if not 1 <= sid <= len(self.sents):
            raise VtError(f"S{sid} out of range (S1-S{len(self.sents)})")
        return self.sents[sid - 1]

    def sent_text(self, sid: int, mark: tuple[int, int] | None = None, latin: bool = False) -> str:
        s = self.sent(sid)
        out = []
        for i in range(s[0], s[1]):
            w = to_latin(self.words[i][0]) if latin and not self.native[i] else self.words[i][0]
            if mark and i == mark[0]:
                w = "[" + w
            if mark and i == mark[1]:
                w = w + "]"
            out.append(w)
        return " ".join(out)

    def lines(self, a: int = 1, b: int | None = None, latin: bool = False) -> list[str]:
        b = min(b or len(self.sents), len(self.sents))
        return [f"S{k} {fmt_t(self.sents[k - 1][2])} {self.sent_text(k, latin=latin)}"
                for k in range(max(a, 1), b + 1)]

    @property
    def mostly_native_latin(self) -> bool:
        return sum(self.native) >= 0.8 * max(1, len(self.native))

    # --- search ---
    def _span(self, sids: tuple[int, int] | None) -> tuple[int, int]:
        if not sids:
            return 0, len(self.words)
        return self.sent(sids[0])[0], self.sent(sids[1])[1]

    def find(self, phrase: str, sids: tuple[int, int] | None = None, fuzzy: float = 0.0,
             cross: float = 0.55) -> list[Hit]:
        """Exact (normalized / romanised) matches; if none and fuzzy>0, best fuzzy windows.
        Score = mean(ratio of romanised text, ratio of consonant skeleton); threshold `fuzzy` for same-script
        windows, `cross` when the phrase is Latin and the window is another script (e.g. Devanagari)."""
        toks = [t for t in (norm_word(x) for x in phrase.split()) if t]
        if not toks:
            raise VtError("empty phrase")
        lo, hi = self._span(sids)
        n = len(toks)
        hits: list[Hit] = []
        occ: dict[int, int] = {}
        for i in range(lo, hi - n + 1):
            if self.norm[i:i + n] == toks:
                hits.append(self._hit(i, i + n - 1, occ))
        if not hits:
            # compound tolerance: "free tier" vs "freetier", "88,000" vs "88 000"
            joined = "".join(toks)
            for i in range(lo, hi):
                acc = ""
                for j in range(i, min(i + n + 2, hi)):
                    acc += self.norm[j]
                    if acc == joined:
                        hits.append(self._hit(i, j, occ))
                        break
                    if not joined.startswith(acc):
                        break
        tlat = re.sub(r"[^a-z0-9]", "", to_latin(phrase))
        if not hits and tlat:
            # romanised exact: 'Amsterdam' vs a transcript already in Latin but split/punctuated differently
            for i in range(lo, hi):
                acc = ""
                for j in range(i, min(i + n + 2, hi)):
                    acc += self.lat[j]
                    if acc == tlat:
                        hits.append(self._hit(i, j, occ))
                        break
                    if not tlat.startswith(acc):
                        break
        if hits or not fuzzy:
            return hits
        tsk = skeleton(tlat)
        latin_target = is_latin(phrase)
        cands = []
        for i in range(lo, hi):
            for m in range(max(1, n - 1), n + 2):
                if i + m > hi:
                    continue
                win = "".join(self.lat[i:i + m])
                if not win:
                    continue
                r = (difflib.SequenceMatcher(None, tlat, win).ratio()
                     + difflib.SequenceMatcher(None, tsk, skeleton(win)).ratio()) / 2
                thr = cross if (latin_target and not all(self.native[i:i + m])) else fuzzy
                if r >= thr:
                    cands.append((r, i, i + m - 1))
        cands.sort(key=lambda c: -c[0])
        used: set[int] = set()
        out = []
        for r, i, j in cands:
            if any(k in used for k in range(i, j + 1)):
                continue
            used.update(range(i, j + 1))
            h = self._hit(i, j, {})
            h.score = r
            out.append(h)
        return out

    def _hit(self, i: int, j: int, occ: dict[int, int]) -> Hit:
        sid = self.sid_of[i]
        occ[sid] = occ.get(sid, 0) + 1
        return Hit(sid, occ[sid], i, j, self.words[i][1], self.words[j][2])

    def context(self, h: Hit, width: int = 70, latin: bool = False) -> str:
        txt = self.sent_text(h.sid, (h.i0, h.i1), latin=latin)
        if len(txt) <= width:
            return txt
        k = txt.find("[")
        a = max(0, k - width // 3)
        return ("…" if a else "") + txt[a:a + width] + "…"


def parse_range(r: str | None) -> tuple[int, int] | None:
    """'S10-S20' | 'S10' | '10-20' -> (10, 20)."""
    if not r:
        return None
    m = re.fullmatch(r"S?(\d+)(?:\s*-\s*S?(\d+))?", r.strip(), re.I)
    if not m:
        raise VtError(f"bad range '{r}' (use S10-S20)")
    a = int(m.group(1))
    return a, int(m.group(2) or a)
