"""Project discovery and layout. Projects live outside the tracked code (see README)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .util import VtError, load_yaml

REPO = Path(__file__).resolve().parents[1]
PKG = Path(__file__).resolve().parent
SPEC = "spec.yaml"
ASSETS = "assets.yaml"


def local_config() -> dict:
    p = REPO / "vt.local.toml"
    if p.exists():
        with open(p, "rb") as f:
            return tomllib.load(f)
    return {}


def projects_root(cli_root: str | None = None) -> Path:
    if cli_root:
        return Path(cli_root).expanduser().resolve()
    if os.environ.get("VT_PROJECTS"):
        return Path(os.environ["VT_PROJECTS"]).expanduser().resolve()
    cfg = local_config()
    if cfg.get("projects_root"):
        return Path(cfg["projects_root"]).expanduser().resolve()
    return REPO / "projects"


@dataclass
class Project:
    dir: Path

    @property
    def name(self) -> str:
        return self.dir.name

    @property
    def spec_path(self) -> Path:
        return self.dir / SPEC

    @property
    def assets_path(self) -> Path:
        return self.dir / ASSETS

    @property
    def cache(self) -> Path:
        p = self.dir / ".vt"
        p.mkdir(exist_ok=True)
        return p

    @property
    def out(self) -> Path:
        p = self.dir / "out"
        p.mkdir(exist_ok=True)
        return p

    @property
    def presets_dir(self) -> Path:
        return self.dir / "presets"

    def assets(self) -> dict[str, str]:
        return dict(load_yaml(self.assets_path) or {})

    def asset_path(self, aid: str) -> Path:
        a = self.assets()
        if aid not in a:
            raise VtError(f"unknown asset '{aid}' (vt assets)")
        raw = str(a[aid]).split("#")[0]
        p = Path(raw)
        return p if p.is_absolute() else self.dir / p


def is_project(d: Path) -> bool:
    return (d / SPEC).is_file() or (d / ASSETS).is_file()


def list_projects(root: Path) -> list[Project]:
    if not root.exists():
        return []
    return [Project(d) for d in sorted(root.iterdir()) if d.is_dir() and is_project(d)]


def active_file(root: Path) -> Path:
    return root / ".vt-active"


def set_active(root: Path, name: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    active_file(root).write_text(name, encoding="utf-8")


def resolve_project(arg: str | None, root: Path) -> Project:
    if arg:
        p = Path(arg)
        for cand in ([p] if p.is_absolute() else [root / arg, Path.cwd() / arg]):
            if cand.is_dir() and is_project(cand):
                return Project(cand.resolve())
        raise VtError(f"no project '{arg}' under {root} (vt ls)")
    af = active_file(root)
    if af.exists():
        name = af.read_text(encoding="utf-8").strip()
        if name and is_project(root / name):
            return Project(root / name)
    if is_project(Path.cwd()):
        return Project(Path.cwd())
    raise VtError("no project selected: pass -p NAME or run `vt use NAME`")
