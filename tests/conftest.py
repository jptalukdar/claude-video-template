import json
import subprocess
from pathlib import Path

import pytest

from vt.assets import add_asset
from vt.project import Project
from vt.util import dump_yaml

# words: [text, start, end, prob] — "the" repeats inside S2, "free" repeats across S2/S3
WORDS = [
    ["Welcome", 0.20, 0.60, 1], ["to", 0.62, 0.70, 1], ["the", 0.72, 0.80, 1], ["demo.", 0.82, 1.20, 1],
    ["The", 1.60, 1.70, 1], ["free", 1.72, 1.95, 1], ["plan", 1.97, 2.20, 1], ["beats", 2.22, 2.50, 1],
    ["the", 2.52, 2.60, 1], ["rest.", 2.62, 3.00, 1],
    ["The", 3.40, 3.50, 1], ["free", 3.52, 3.70, 1], ["tier", 3.72, 3.95, 1], ["has", 3.97, 4.10, 1],
    ["limits.", 4.12, 4.60, 1],
    ["आमस्तुदाम", 4.90, 5.40, 1], ["की", 5.42, 5.50, 1], ["नहरें.", 5.52, 5.90, 1],
]


def make_video(path: Path, dur: float = 6.0, size: str = "640x360", fps: int = 25, audio: bool = True):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={dur}", "-ac", "2"]
    cmd += ["-c:v", "libx264", "-profile:v", "main", "-pix_fmt", "yuv420p", "-b:v", "800k"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "96k"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True)


def make_png(path: Path, w=400, h=200, color=(200, 40, 40, 255)):
    from PIL import Image
    Image.new("RGBA", (w, h), color).save(path)


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("media") / "src.mp4"
    make_video(p)
    return p


@pytest.fixture
def proj(tmp_path, sample_video) -> Project:
    d = tmp_path / "p1"
    d.mkdir()
    pr = Project(d)
    dump_yaml({}, pr.assets_path)
    add_asset(pr, sample_video, "main")
    make_png(tmp_path / "logo.png")
    make_png(tmp_path / "card.png", 640, 360, (20, 20, 120, 255))
    add_asset(pr, tmp_path / "logo.png", "logo")
    add_asset(pr, tmp_path / "card.png", "card")
    (pr.cache / "words.json").write_text(json.dumps({"key": "k", "model": "fake", "lang": "en", "device": "cpu",
                                                     "words": WORDS}), encoding="utf-8")
    pr.spec_path.write_text("# my comment\nsrc: main\n", encoding="utf-8")
    return pr
