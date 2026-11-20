"""Text -> RGBA PNG with Pillow (style presets, custom fonts, sub line, box, stroke, shadow)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..util import VtError, short_hash

FAMILIES = {
    "sans": ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc", "LiberationSans-Regular.ttf"],
    "sans-bold": ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "Helvetica.ttc#1",
                  "LiberationSans-Bold.ttf"],
    "sans-black": ["seguibl.ttf", "ariblk.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"],
    "serif": ["georgia.ttf", "times.ttf", "DejaVuSerif.ttf", "Times.ttc"],
    "mono": ["consola.ttf", "cour.ttf", "DejaVuSansMono.ttf", "Menlo.ttc"],
}


def _font_dirs() -> list[Path]:
    dirs = []
    if os.name == "nt":
        dirs += [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]
    elif sys.platform == "darwin":
        dirs += [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts"]
    return [d for d in dirs if d.exists()]


def _find_file(name: str) -> Path | None:
    for d in _font_dirs():
        p = d / name
        if p.exists():
            return p
        hits = list(d.rglob(name)) if d.name != "Fonts" or os.name != "nt" else []
        if hits:
            return hits[0]
    return None


def resolve_font(name: str, assets: dict[str, str], proj_dir: Path | None) -> tuple[Path, int]:
    """Font id (manifest) | file path | family name | system file name -> (path, face index)."""
    def split(s: str) -> tuple[str, int]:
        if "#" in s:
            a, b = s.rsplit("#", 1)
            return a, int(b)
        return s, 0

    if name in assets:
        p, idx = split(str(assets[name]))
        path = Path(p) if Path(p).is_absolute() else (proj_dir / p if proj_dir else Path(p))
        if not path.exists():
            raise VtError(f"font asset '{name}' missing file {path}")
        return path, idx
    p, idx = split(name)
    if Path(p).exists():
        return Path(p), idx
    for cand in FAMILIES.get(name, [name]):
        c, ci = split(cand)
        f = _find_file(c)
        if f:
            return f, ci
    raise VtError(f"font '{name}' not found (add it: vt assets add path/to/font.ttf)")


def _rgba(c: str | None, default=(255, 255, 255, 255)):
    if not c:
        return default
    c = str(c).lstrip("#")
    if len(c) == 6:
        c += "FF"
    if len(c) != 8:
        raise VtError(f"bad colour '#{c}' (use #RRGGBB or #RRGGBBAA)")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4, 6))


def missing_glyphs(text: str, font_path: Path, idx: int = 0) -> str:
    f = ImageFont.truetype(str(font_path), 32, index=idx)
    notdef = f.getmask("\U0010FFFD")
    nd = (notdef.size, bytes(notdef))
    miss = []
    for ch in set(text):
        if ch.isspace():
            continue
        m = f.getmask(ch)
        if (m.size, bytes(m)) == nd:
            miss.append(ch)
    return "".join(sorted(miss))


def _wrap(text: str, font, maxw: float) -> list[str]:
    out = []
    for para in text.split("\n"):
        words, line = para.split(" "), ""
        for w in words:
            t = (line + " " + w).strip()
            if line and font.getlength(t) > maxw:
                out.append(line)
                line = w
            else:
                line = t
        out.append(line)
    return out


def render_text(text: str, sub: str | None, style: dict, font_main: tuple[Path, int],
                font_sub: tuple[Path, int], W: int, H: int, cache: Path) -> Path:
    """Render to a cached PNG sized tightly around the text block; returns its path."""
    key = short_hash(text, sub, sorted(style.items(), key=lambda kv: kv[0]), str(font_main), str(font_sub), W, H)
    out = cache / "text" / f"{key}.png"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    img = text_image(text, sub, style, font_main, font_sub, W, H)
    img.save(out)
    return out


def text_image(text, sub, style, font_main, font_sub, W, H) -> Image.Image:
    px = max(6, round(float(style.get("size", 0.05)) * H))
    f = ImageFont.truetype(str(font_main[0]), px, index=font_main[1])
    if style.get("upper"):
        text = text.upper()
    maxw = float(style.get("max_w", 0.9)) * W
    stroke = round(float(style.get("stroke", 0) or 0) * px)
    lines = [(ln, f, _rgba(style.get("color"))) for ln in _wrap(str(text), f, maxw)]
    sst = style.get("sub") or {}
    if sub:
        spx = max(6, round(float(sst.get("size", 0.6 * float(style.get("size", 0.05)))) * H))
        sf = ImageFont.truetype(str(font_sub[0]), spx, index=font_sub[1])
        s = str(sub).upper() if sst.get("upper") else str(sub)
        sl = [(ln, sf, _rgba(sst.get("color") or style.get("color"))) for ln in _wrap(s, sf, maxw)]
        lines = sl + lines if sst.get("above") else lines + sl
    gap = float(style.get("line_gap", 1.12))
    metrics = []
    for ln, fo, col in lines:
        asc, desc = fo.getmetrics()
        l, t, r, b = fo.getbbox(ln or " ", stroke_width=stroke if fo is f else 0, anchor="ls")
        metrics.append((ln, fo, col, max(r, 1), asc, desc))
    block_w = max(m[3] for m in metrics) + 2 * stroke
    heights = [round((m[4] + m[5]) * (gap if i < len(metrics) - 1 else 1.0)) for i, m in enumerate(metrics)]
    block_h = sum(heights) + 2 * stroke
    pad = round(float(style.get("pad", 0) or 0) * px) if style.get("box") else 0
    has_shadow = bool(style.get("shadow"))
    sh_off = max(1, round(px * 0.05)) if has_shadow else 0
    sh_blur = max(1, round(px * 0.08)) if has_shadow else 0
    margin = (sh_off + 2 * sh_blur) if has_shadow else 0
    iw, ih = block_w + 2 * pad + 2 * margin, block_h + 2 * pad + 2 * margin
    iw += iw % 2
    ih += ih % 2
    img = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    if style.get("box"):
        d = ImageDraw.Draw(img)
        rad = round(float(style.get("radius", 0) or 0) * px)
        d.rounded_rectangle([margin, margin, margin + block_w + 2 * pad, margin + block_h + 2 * pad], rad,
                            fill=_rgba(style["box"]))
    txt = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)
    align = style.get("align", "c")
    y = margin + pad + stroke
    for (ln, fo, col, w, asc, desc), h in zip(metrics, heights):
        if align == "l":
            x = margin + pad + stroke
        elif align == "r":
            x = margin + pad + stroke + (block_w - 2 * stroke - w)
        else:
            x = margin + pad + stroke + (block_w - 2 * stroke - w) / 2
        sw = stroke if fo is f else 0
        d.text((x, y + asc), ln, font=fo, fill=col, anchor="ls", stroke_width=sw,
               stroke_fill=_rgba(style.get("stroke_color"), (0, 0, 0, 255)) if sw else None)
        y += h
    if has_shadow:
        a = txt.split()[3]
        shadow = Image.new("RGBA", (iw, ih), _rgba(style["shadow"]))
        mask = Image.new("L", (iw, ih), 0)
        mask.paste(a, (sh_off, sh_off))
        mask = mask.filter(ImageFilter.GaussianBlur(sh_blur))
        sa = _rgba(style["shadow"])[3] / 255
        mask = mask.point(lambda v: int(v * sa))
        shadow.putalpha(mask)
        img = Image.alpha_composite(img, shadow)
    return Image.alpha_composite(img, txt)


def text_card(text: str, style: dict, font: tuple[Path, int], W: int, H: int, bg: str, cache: Path) -> Path:
    """Full-frame text card (e.g. disclaimer) on a solid background."""
    key = short_hash("card", text, sorted(style.items(), key=lambda kv: kv[0]), str(font), W, H, bg)
    out = cache / "text" / f"{key}.png"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGBA", (W, H), _rgba(bg, (0, 0, 0, 255)))
    t = text_image(text, None, style, font, font, W, H)
    canvas.alpha_composite(t, ((W - t.width) // 2, (H - t.height) // 2))
    canvas.convert("RGB").save(out)
    return out
