"""Cross-script matching: Indic scripts -> rough Latin, plus a phonetic skeleton for fuzzy search.

Lets an anchor written in Latin ("Amsterdam") find words Whisper wrote in Devanagari/Bengali/... (अमस्टर्दाम).
All major Indic blocks share the ISCII layout, so they are folded onto Devanagari by code-point offset.
"""
from __future__ import annotations

import re
import unicodedata

DEVA = 0x0900
INDIC_BLOCKS = range(0x0900, 0x0D80)  # Devanagari .. Malayalam (each block 0x80 wide)

CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "ऩ": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ऱ": "r", "ल": "l",
    "ळ": "l", "ऴ": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f", "य़": "y",
}
VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai",
          "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e", "ऎ": "e", "ऒ": "o"}
MATRAS = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o",
          "ौ": "au", "ॉ": "o", "ॅ": "e", "ॆ": "e", "ॊ": "o"}
VIRAMA, NUKTA = "्", "़"
SIGNS = {"ं": "n", "ँ": "n", "ः": "h"}
EXTRA = {"ৰ": "r", "ৱ": "w", "ৎ": "t"}  # Assamese/Bengali specials outside the shared layout


def _fold(ch: str) -> str:
    cp = ord(ch)
    if ch in EXTRA:
        return ch
    if cp in INDIC_BLOCKS and cp >= 0x0980:
        return chr(DEVA + (cp - DEVA) % 0x80)
    return ch


def is_latin(s: str) -> bool:
    return all(ord(c) < 0x250 for c in s if c.isalpha())


def to_latin(s: str) -> str:
    """Rough romanisation of Indic text; Latin text passes through (accents stripped)."""
    if is_latin(s):
        return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = unicodedata.normalize("NFC", "".join(_fold(c) for c in s))
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if i + 1 < n and s[i + 1] == NUKTA and (c + NUKTA) in CONS:
            c, i = c + NUKTA, i + 1
        if c in EXTRA:
            out.append(EXTRA[c])
            nxt = s[i + 1] if i + 1 < n else ""
            if nxt not in MATRAS and nxt != VIRAMA:
                out.append("a")
        elif c in CONS:
            out.append(CONS[c])
            nxt = s[i + 1] if i + 1 < n else ""
            if nxt == NUKTA:
                i += 1
                nxt = s[i + 1] if i + 1 < n else ""
            if nxt not in MATRAS and nxt != VIRAMA:
                out.append("a")  # inherent vowel (schwa)
        elif c in VOWELS:
            out.append(VOWELS[c])
        elif c in MATRAS:
            out.append(MATRAS[c])
        elif c in SIGNS:
            out.append(SIGNS[c])
        elif c == VIRAMA or c == NUKTA:
            pass
        elif c.isspace():
            out.append(" ")
        elif c.isascii():
            out.append(c.lower())
        i += 1
    txt = "".join(out)
    # schwa deletion at word ends ("kamala" -> "kamal")
    return re.sub(r"(?<=[^aeiou\s])a\b", "", txt)


_DIGRAPHS = [("chh", "C"), ("ch", "C"), ("sh", "S"), ("ph", "f"), ("bh", "b"), ("kh", "k"), ("gh", "g"),
             ("jh", "j"), ("th", "t"), ("dh", "d"), ("rh", "r"), ("ck", "k"), ("q", "k"), ("x", "ks"),
             ("w", "v"), ("z", "j"), ("c", "k"), ("C", "c"), ("S", "s")]


def skeleton(latin: str) -> str:
    """Consonant skeleton: digraphs folded, vowels dropped (except leading), repeats collapsed."""
    s = re.sub(r"[^a-z]", "", latin.lower())
    if not s:
        return ""
    for a, b in _DIGRAPHS:
        s = s.replace(a, b)
    head, rest = s[0], re.sub(r"[aeiouy]", "", s[1:])
    out = head + rest
    return re.sub(r"(.)\1+", r"\1", out)
