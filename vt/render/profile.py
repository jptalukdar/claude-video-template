"""Output encoding profile. Default = match the source's technical spec exactly."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..probe import fps_float
from ..util import VtError

X264_PROFILES = {"baseline": "baseline", "constrained baseline": "baseline", "main": "main", "high": "high",
                 "high 10": "high10", "high 4:2:2": "high422", "high 4:4:4 predictive": "high444"}
X265_PROFILES = {"main": "main", "main 10": "main10", "rext": "main444-8"}
TRANSFER = {"bt709": "bt709", "smpte170m": "smpte170m", "bt470bg": "bt470bg", "smpte2084": "smpte2084",
            "arib-std-b67": "arib-std-b67", "iec61966-2-1": "iec61966-2-1"}


@dataclass
class Profile:
    W: int
    H: int
    fps: str
    sar: str = "1:1"
    vcodec: str = "libx264"
    vprofile: str | None = None
    level: str | None = None
    pix_fmt: str = "yuv420p"
    color_range: str | None = None
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    vbr: int | None = None
    crf: float | None = None
    preset: str = "medium"
    two_pass: bool = False
    timescale: int | None = None
    acodec: str = "aac"
    sr: int = 48000
    channels: int = 2
    layout: str = "stereo"
    abr: int | None = 128000
    preview: bool = False
    notes: list = field(default_factory=list)

    @property
    def fps_f(self) -> float:
        return fps_float(self.fps)

    @property
    def matrix(self) -> str:
        """swscale colour matrix name for RGB<->YUV conversions."""
        cs = (self.color_space or "").lower()
        if cs in ("bt709",):
            return "bt709"
        if cs in ("bt2020nc", "bt2020c"):
            return "bt2020"
        if cs in ("smpte170m", "bt470bg"):
            return "bt601"
        return "bt709" if self.H >= 720 else "bt601"

    @property
    def range_tv(self) -> bool:
        return (self.color_range or "tv") != "pc"

    def encode_args(self) -> list[str]:
        a = ["-c:v", self.vcodec, "-pix_fmt", self.pix_fmt, "-r", self.fps]
        if self.vcodec in ("libx264", "libx265"):
            a += ["-preset", "ultrafast" if self.preview else self.preset]
        if self.vprofile and not self.preview:
            a += ["-profile:v", self.vprofile]
        if self.level and not self.preview:
            a += (["-level", self.level] if self.vcodec == "libx264" else ["-x265-params", f"level-idc={self.level}"])
        if self.preview:
            a += ["-crf", "30"]
        elif self.crf is not None:
            a += ["-crf", str(self.crf)]
        elif self.vbr:
            a += ["-b:v", str(self.vbr), "-maxrate", str(int(self.vbr * 1.5)), "-bufsize", str(int(self.vbr * 2))]
        for opt, val in (("-color_range", self.color_range), ("-colorspace", self.color_space),
                         ("-color_trc", self.color_transfer), ("-color_primaries", self.color_primaries)):
            if val and val != "unknown":
                a += [opt, val]
        if self.vcodec == "libx265":
            a += ["-tag:v", "hvc1"]
        if self.timescale:
            a += ["-video_track_timescale", str(self.timescale)]
        a += ["-c:a", self.acodec, "-ar", str(self.sr), "-ac", str(self.channels)]
        if self.abr:
            a += ["-b:a", str(self.abr if not self.preview else min(self.abr, 96000))]
        a += ["-movflags", "+faststart"]
        return a


def _level_str(codec: str, level) -> str | None:
    if level in (None, -99, "unknown"):
        return None
    lv = int(level)
    if codec == "h264":
        return f"{lv // 10}.{lv % 10}" if lv >= 10 else str(lv)
    if codec == "hevc":
        return f"{lv / 30:g}"
    return None


def build_profile(probe: dict, out: dict | None, preview: bool = False, preview_w: int = 854) -> Profile:
    out = dict(out or {})
    v, a = probe.get("v") or {}, probe.get("a") or {}
    if not v:
        raise VtError("source has no video stream")
    W, H = int(v["w"]), int(v["h"])
    fps = str(v.get("fps") or "30/1")
    p = Profile(W=W, H=H, fps=fps, sar=v.get("sar") or "1:1")
    codec = v.get("codec")
    if codec == "h264":
        p.vcodec, p.vprofile = "libx264", X264_PROFILES.get(str(v.get("profile", "")).lower())
    elif codec == "hevc":
        p.vcodec, p.vprofile = "libx265", X265_PROFILES.get(str(v.get("profile", "")).lower())
    else:
        p.notes.append(f"source codec {codec} not re-encodable as-is; using libx264")
    p.level = _level_str(codec, v.get("level"))
    p.pix_fmt = v.get("pix_fmt") or "yuv420p"
    p.color_range, p.color_space = v.get("color_range"), v.get("color_space")
    p.color_transfer = TRANSFER.get(v.get("color_transfer") or "", v.get("color_transfer"))
    p.color_primaries = v.get("color_primaries")
    p.vbr = v.get("bit_rate")
    if p.vbr is None:
        p.crf = 18
    tb = v.get("time_base") or ""
    if tb.startswith("1/"):
        p.timescale = int(tb[2:])
    if a:
        p.acodec = "aac" if a.get("codec") == "aac" else "aac"
        if a.get("codec") != "aac":
            p.notes.append(f"source audio {a.get('codec')} -> aac")
        p.sr, p.channels = int(a.get("sample_rate") or 48000), int(a.get("channels") or 2)
        p.layout = a.get("channel_layout") or ("stereo" if p.channels == 2 else "mono")
        p.abr = a.get("bit_rate") or 128000
    # explicit overrides
    if out.get("fps"):
        p.fps = str(out["fps"])
    if out.get("size"):
        try:
            w, h = (int(x) for x in str(out["size"]).lower().split("x"))
        except ValueError:
            raise VtError("out.size must be WxH")
        p.W, p.H = w, h
        p.level = None
    if out.get("crf") is not None:
        p.crf, p.vbr = float(out["crf"]), None
    if out.get("vbr"):
        p.vbr, p.crf = int(out["vbr"]), None
    if out.get("abr"):
        p.abr = int(out["abr"])
    if out.get("preset"):
        p.preset = str(out["preset"])
    if out.get("vcodec"):
        p.vcodec = str(out["vcodec"])
    # source-matched bitrate needs two-pass ABR to land within tolerance; out.two_pass: false disables
    p.two_pass = bool(out.get("two_pass", p.vbr is not None))
    if preview:
        p.preview = True
        if p.W > preview_w:
            p.H = round(p.H * preview_w / p.W / 2) * 2
            p.W = preview_w
    p.W -= p.W % 2
    p.H -= p.H % 2
    return p
