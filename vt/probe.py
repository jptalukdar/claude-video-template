"""Source technical spec (ffprobe) — the default output profile matches it exactly."""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from .util import ffprobe_json, read_json, write_json, file_fingerprint


def _num(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default


def probe(path: Path) -> dict:
    j = ffprobe_json(path)
    fmt = j.get("format", {})
    v = next((s for s in j["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in j["streams"] if s.get("codec_type") == "audio"), None)
    out: dict = {"duration": float(fmt.get("duration", 0) or 0), "format": fmt.get("format_name"),
                 "brand": (fmt.get("tags") or {}).get("major_brand")}
    if v:
        sar = v.get("sample_aspect_ratio") or "1:1"
        if sar in ("N/A", "0:1"):
            sar = "1:1"
        out["v"] = {
            "codec": v.get("codec_name"), "profile": v.get("profile"), "level": v.get("level"),
            "w": v.get("width"), "h": v.get("height"), "sar": sar,
            "fps": v.get("r_frame_rate"), "pix_fmt": v.get("pix_fmt"),
            "color_range": v.get("color_range"), "color_space": v.get("color_space"),
            "color_transfer": v.get("color_transfer"), "color_primaries": v.get("color_primaries"),
            "field_order": v.get("field_order"), "time_base": v.get("time_base"),
            "bit_rate": _num(v.get("bit_rate")), "duration": _num(v.get("duration")),
        }
    if a:
        out["a"] = {
            "codec": a.get("codec_name"), "profile": a.get("profile"),
            "sample_rate": _num(a.get("sample_rate")), "channels": a.get("channels"),
            "channel_layout": a.get("channel_layout") or ("stereo" if a.get("channels") == 2 else "mono"),
            "bit_rate": _num(a.get("bit_rate")),
        }
    return out


def fps_float(fps: str | float) -> float:
    if isinstance(fps, (int, float)):
        return float(fps)
    return float(Fraction(fps))


def cached_probe(path: Path, cache: Path) -> dict:
    """Probe once per file content; stored as .vt/probe.json."""
    f = cache / "probe.json"
    fp = file_fingerprint(path)
    d = read_json(f)
    if d and d.get("_fp") == fp:
        return d
    d = probe(path)
    d["_fp"] = fp
    write_json(f, d, compact=False)
    return d


COMPARE = [("v", k) for k in ("codec", "profile", "level", "w", "h", "sar", "fps", "pix_fmt", "color_range",
                              "color_space", "color_transfer", "color_primaries", "field_order")] + \
          [("a", k) for k in ("codec", "profile", "sample_rate", "channels", "channel_layout")]


# x264 can only emit Constrained Baseline (a strict subset every Baseline decoder plays)
EQUIV = {"constrained baseline": "baseline"}


def _eq(k: str, a, b) -> bool:
    if k == "profile" and isinstance(a, str) and isinstance(b, str):
        return EQUIV.get(a.lower(), a.lower()) == EQUIV.get(b.lower(), b.lower())
    return a == b


def diff_spec(src: dict, out: dict, br_tol: float = 0.10) -> list[str]:
    """Human-readable list of tech-spec differences (empty == match)."""
    diffs = []
    for sec, k in COMPARE:
        a, b = (src.get(sec) or {}).get(k), (out.get(sec) or {}).get(k)
        if not _eq(k, a, b):
            diffs.append(f"{sec}.{k}: src={a} out={b}")
    # audio is encoded with the source bitrate as target, but AAC spends fewer bits on silence/simple
    # content (e.g. silent cards), so its measured rate is not a spec mismatch
    for sec in ("v",):
        a, b = (src.get(sec) or {}).get("bit_rate"), (out.get(sec) or {}).get("bit_rate")
        if a and b and abs(b - a) / a > br_tol:
            diffs.append(f"{sec}.bit_rate: src={a} out={b} ({(b - a) / a:+.0%})")
    return diffs
