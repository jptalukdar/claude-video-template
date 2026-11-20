"""Asset manifest (assets.yaml): short id -> path inside the project folder."""
from __future__ import annotations

import shutil
from pathlib import Path

from .project import Project
from .util import VtError, dump_yaml, load_yaml, slug

KINDS = {
    "video": {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"},
    "img": {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"},
    "font": {".ttf", ".otf", ".ttc"},
    "audio": {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"},
}
FOLDER = {"video": "media", "img": "images", "font": "fonts", "audio": "media"}


def kind_of(path: str | Path) -> str:
    ext = Path(str(path).split("#")[0]).suffix.lower()
    for k, exts in KINDS.items():
        if ext in exts:
            return k
    return "other"


def add_asset(proj: Project, src: Path, aid: str | None = None, copy: bool = True) -> str:
    src = Path(src)
    face = ""
    if "#" in src.name:  # font.ttc#1
        face = "#" + src.name.split("#")[1]
        src = src.with_name(src.name.split("#")[0])
    if not src.exists():
        raise VtError(f"file not found: {src}")
    kind = kind_of(src)
    if kind == "other":
        raise VtError(f"unsupported asset type: {src.suffix}")
    manifest = load_yaml(proj.assets_path, rt=True) or {}
    auto = aid is None
    aid = aid or slug(src.stem)
    if auto and aid in manifest:
        base, i = aid, 2
        while f"{base}{i}" in manifest:
            i += 1
        aid = f"{base}{i}"
    if copy:
        dest_dir = proj.dir / FOLDER[kind]
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / src.name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        rel = dest.relative_to(proj.dir).as_posix()
    else:
        rel = str(src.resolve())
    manifest[aid] = rel + face
    dump_yaml(manifest, proj.assets_path)
    return aid


def list_assets(proj: Project) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for aid, p in proj.assets().items():
        out.setdefault(kind_of(p), []).append(aid)
    return out
