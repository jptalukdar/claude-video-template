"""Small shared helpers: errors, time formatting, YAML I/O, hashing, subprocess."""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


class VtError(Exception):
    """User-facing error; printed as a single line by the CLI."""


# ---------- time ----------

def fmt_t(t: float, prec: int = 1) -> str:
    """83.42 -> '1:23.4'."""
    neg = t < 0
    t = abs(t)
    m, s = divmod(t, 60)
    out = f"{int(m)}:{s:0{3 + prec if prec else 2}.{prec}f}"
    return "-" + out if neg else out


def parse_t(s: str | float | int) -> float:
    """'1:23.4' | '83.4' | 83.4 -> seconds."""
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip()
    if ":" in s:
        parts = [float(p) for p in s.split(":")]
        t = 0.0
        for p in parts:
            t = t * 60 + p
        return t
    return float(s)


# ---------- yaml ----------

def yaml_rt() -> YAML:
    y = YAML(typ="rt")
    y.width = 4096
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=2, offset=0)
    return y


def yaml_safe() -> YAML:
    return YAML(typ="safe", pure=True)


def load_yaml(path: Path, rt: bool = False) -> Any:
    if not path.exists():
        return None
    y = yaml_rt() if rt else yaml_safe()
    with open(path, encoding="utf-8") as f:
        return y.load(f)


def dump_yaml(data: Any, path: Path) -> None:
    y = yaml_rt()
    buf = io.StringIO()
    y.dump(data, buf)
    path.write_text(buf.getvalue(), encoding="utf-8")


def parse_scalar(v: str) -> Any:
    """Parse a CLI value with YAML semantics ('1.5'->1.5, '{x: 1}'->dict); fall back to str."""
    try:
        out = yaml_safe().load(v)
    except Exception:
        return v
    if out is None and v.strip() not in ("null", "~", ""):
        return v
    return out


# ---------- json ----------

def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any, compact: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    txt = json.dumps(data, separators=(",", ":") if compact else None, indent=None if compact else 1,
                     ensure_ascii=False)
    path.write_text(txt, encoding="utf-8")


# ---------- hashing ----------

def file_fingerprint(path: Path, chunk: int = 4 << 20) -> str:
    """Fast content fingerprint: size + first/last `chunk` bytes."""
    h = hashlib.sha1()
    size = path.stat().st_size
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(chunk))
        if size > chunk:
            f.seek(max(size - chunk, chunk))
            h.update(f.read(chunk))
    return h.hexdigest()


def short_hash(*parts: Any) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(repr(p).encode("utf-8"))
    return h.hexdigest()[:12]


# ---------- text ----------

_NORM_RE = re.compile(r"[^\w]+", re.UNICODE)


def norm_word(w: str) -> str:
    """Lowercase, strip punctuation (keeps letters/digits of any script)."""
    return _NORM_RE.sub("", w.lower()).replace("_", "")


def slug(s: str, maxlen: int = 16) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return (s or "a")[:maxlen].rstrip("_") or "a"


# ---------- processes ----------

def need_tool(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise VtError(f"{name} not found on PATH")
    return p


def run(cmd: list[str], capture: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    cp = subprocess.run(cmd, capture_output=capture, text=True, encoding="utf-8", errors="replace")
    if check and cp.returncode != 0:
        tail = (cp.stderr or "").strip().splitlines()[-6:]
        raise VtError(f"{Path(cmd[0]).stem} failed: " + " | ".join(tail))
    return cp


def ffprobe_json(path: Path) -> dict:
    cp = run([need_tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
              str(path)])
    return json.loads(cp.stdout)
