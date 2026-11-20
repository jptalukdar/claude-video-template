"""Bulk operations across projects (transcribe / check / render)."""
from __future__ import annotations

import fnmatch
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .project import Project, list_projects, resolve_project
from .util import VtError


def select(root: Path, names: list[str], all_: bool, glob: str | None) -> list[Project]:
    if all_ or glob:
        ps = list_projects(root)
        if glob:
            ps = [p for p in ps if fnmatch.fnmatch(p.name, glob)]
        return ps
    return [resolve_project(n, root) for n in names]


def run_many(projs: list[Project], fn: Callable[[Project], str], jobs: int = 1, fail_fast: bool = False,
             log: Path | None = None) -> tuple[list[str], int]:
    """fn returns a short status string or raises VtError. Returns (lines, n_failed)."""
    lines, failed = [], 0
    t0 = time.time()

    def one(p: Project) -> tuple[Project, str, bool]:
        t = time.time()
        try:
            msg = fn(p)
            return p, f"{msg} {time.time() - t:.0f}s" if not msg.startswith("skip") else msg, True
        except VtError as e:
            return p, f"FAIL {e}", False
        except Exception as e:  # keep going with other projects
            return p, f"FAIL {type(e).__name__}: {e}", False

    results = []
    if jobs <= 1:
        for p in projs:
            r = one(p)
            results.append(r)
            if fail_fast and not r[2]:
                break
    else:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            results = list(ex.map(one, projs))
    w = max([len(p.name) for p in projs] + [4])
    for p, msg, ok in results:
        lines.append(f"{p.name:<{w}}  {msg}")
        failed += 0 if ok else 1
    lines.append(f"{len(results)} projects, {len(results) - failed} ok, {failed} failed, {time.time() - t0:.0f}s")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + "\n".join(lines) + "\n\n")
    return lines, failed
