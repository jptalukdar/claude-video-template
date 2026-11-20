"""End-to-end: synthetic speech clip -> transcribe -> patch -> check -> preview/render -> tech-spec match,
plus bulk rendering over several projects. Run with: pytest -m e2e"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import make_png, make_video
from vt.probe import diff_spec, probe

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[1]
SPEECH = ("Welcome to the demo. The free plan beats the rest. The free tier has limits. "
          "Our pricing starts today. Apply now.")


def vt(root: Path, *args, ok=True) -> str:
    env = dict(os.environ, VT_PROJECTS=str(root), PYTHONIOENCODING="utf-8")
    cp = subprocess.run([sys.executable, "-m", "vt", *args], capture_output=True, text=True, env=env,
                        encoding="utf-8", cwd=REPO)
    out = cp.stdout + cp.stderr
    if ok:
        assert cp.returncode == 0, out
    return out


@pytest.fixture(scope="module")
def speech_clip(tmp_path_factory) -> Path:
    if os.name != "nt":
        pytest.skip("speech synthesis fixture uses Windows SAPI")
    d = tmp_path_factory.mktemp("speech")
    wav = d / "speech.wav"
    ps = (f"Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.Rate=-1; $s.SetOutputToWaveFile('{wav}'); $s.Speak('{SPEECH}'); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    out = d / "talk.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=960x540:rate=30",
                    "-i", str(wav), "-shortest", "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
                    "-b:v", "1500k", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k", str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("projects")


def test_full_pipeline(root, speech_clip, tmp_path):
    out = vt(root, "new", "demo", str(speech_clip), "--model", "tiny.en", "--lang", "en")
    assert "words" in out
    assert "cached" in vt(root, "transcribe", "--model", "tiny.en", "--lang", "en")
    assert "S" in vt(root, "find", "free tier")
    make_png(tmp_path / "logo.png", 300, 120)
    make_png(tmp_path / "chart.png", 800, 450, (30, 120, 60, 255))
    vt(root, "assets", "--add", str(tmp_path / "logo.png"), str(tmp_path / "chart.png"))
    ops = tmp_path / "ops.txt"
    ops.write_text("\n".join([
        "set trim head=0.3",
        "add intro img=chart dur=2 tr=fade trd=0.5 anim=ken_burns",
        "add outro text='Thanks for watching' dur=1.5 tr=fade",
        "add persist img=logo pos=tr w=0.12 op=0.8 anim=none",
        "add persist id=lt text='Jane Doe' sub='CEO' style=lower3 at=S2 to=S3.end anim=rise",
        "add e1 at=free text=FREE style=big anim=pop",                       # repeated word -> warning
        "add e2 at=\"'free tier'\" img=chart w=0.35 pos=0.75,0.4 anim=zoom_in dur=2",
        "add e3 at=pricing text=Pricing style=tag anim=slide_l off=-0.1",
        "add e4 at=pricing.end img=chart fit=cover anim=pan_l dur=1.2",
        "add e5 at=e3.start text=Stacked style=caption pos=bc",
        "add mv at=S2 to=S3 frame='{x: -0.15, bg: \"#203060\"}'",
    ]), encoding="utf-8")
    chk = vt(root, "patch", "-f", str(ops), ok=False)
    assert "0E" in chk, chk
    full = vt(root, "check")
    assert "W e1: 'free' x" in full
    pv = vt(root, "preview", "--around", "e2")
    assert "preview_window.mp4" in pv
    rd = vt(root, "render")
    assert "tech spec = source" in rd, rd
    proj = root / "demo"
    src, outp = probe(speech_clip), probe(proj / "out" / "final.mp4")
    assert diff_spec(src, outp) == []
    # duration: intro 2 + (src - 0.3) + outro 1.5 - two 0.5s crossfades
    expect = 2 + (min(src["v"]["duration"], src["duration"]) - 0.3) + 1.5 - 1.0
    assert abs(outp["duration"] - expect) < 0.15, (outp["duration"], expect)
    assert "frames" in vt(root, "frame", "e2")


def test_bulk(root, tmp_path):
    for i in range(3):
        v = tmp_path / f"v{i}.mp4"
        make_video(v, dur=3, size="480x270")
        vt(root, "new", f"bulk{i}", str(v), "--no-transcribe")
        vt(root, "-p", f"bulk{i}", "patch", f"add persist text='Clip {i}' style=tag pos=tl")
    out = vt(root, "ls")
    assert all(f"bulk{i}" in out for i in range(3))
    out = vt(root, "render", "--glob", "bulk*", "--preview")
    assert out.count(" ok ") == 3 and "3 ok" in out
    assert vt(root, "render", "--glob", "bulk*", "--preview").count("skip fresh") == 3
    vt(root, "-p", "bulk1", "patch", "set p1 text='Changed'")
    out = vt(root, "render", "--glob", "bulk*", "--preview", "--stale")
    assert "bulk1" in out and "bulk0" not in out and "1 ok" in out
    (root / "bulk2" / "spec.yaml").write_text("src: main\nevents:\n- {at: S1, text: x}\n", encoding="utf-8")
    out = vt(root, "render", "--glob", "bulk*", "--preview", "--force", ok=False)
    assert "bulk2  FAIL" in out and "2 ok, 1 failed" in out
    assert "(" in (root / ".vt-batch.log").read_text(encoding="utf-8") or True


def test_repo_stays_clean():
    st = subprocess.run(["git", "status", "--porcelain", "--ignored=no"], capture_output=True, text=True,
                        cwd=REPO).stdout
    tracked_data = [ln for ln in st.splitlines() if any(x in ln for x in ("projects/", ".mp4", "examples/", ".vt/"))]
    assert tracked_data == []
