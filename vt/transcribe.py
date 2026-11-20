"""Speech-to-text with faster-whisper (word timestamps), cached per file content + model + language."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .util import file_fingerprint, read_json, short_hash, write_json, VtError


def _add_cuda_dll_dirs() -> None:
    """On Windows, pip-installed nvidia-cublas/cudnn wheels ship DLLs that must be on the DLL path."""
    if os.name != "nt":
        return
    for base in sys.path:
        nv = Path(base) / "nvidia"
        if nv.is_dir():
            for b in nv.glob("*/bin"):
                try:
                    os.add_dll_directory(str(b))
                    os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")
                except OSError:
                    pass


def _audio(path: Path):
    """Decode to 16 kHz mono float32 with ffmpeg (avoids PyAV version issues)."""
    import numpy as np
    import subprocess
    from .util import need_tool
    cp = subprocess.run([need_tool("ffmpeg"), "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
                         "-ar", "16000", "-f", "f32le", "-"], capture_output=True)
    if cp.returncode != 0:
        raise VtError("audio decode failed: " + cp.stderr.decode(errors="replace").strip()[-300:])
    return np.frombuffer(cp.stdout, dtype=np.float32).copy()


def _run(path: Path, model: str, lang: str | None, device: str, compute: str, audio=None) -> tuple[list, str]:
    from faster_whisper import WhisperModel

    m = WhisperModel(model, device=device, compute_type=compute)
    segs, info = m.transcribe(audio if audio is not None else _audio(path), language=lang,
                              word_timestamps=True, vad_filter=True,
                              beam_size=5, condition_on_previous_text=False)
    words = []
    for s in segs:
        for w in s.words or []:
            txt = w.word.strip()
            if txt:
                words.append([txt, round(w.start, 3), round(w.end, 3), round(w.probability, 3)])
    return words, info.language


def transcribe(path: Path, cache: Path, model: str = "small", lang: str | None = None,
               device: str = "auto", force: bool = False) -> tuple[dict, bool]:
    """Returns (data, was_cached). data = {key, model, lang, device, words: [[w, start, end, prob], ...]}."""
    key = short_hash(file_fingerprint(path), model, lang)
    out = cache / "words.json"
    d = read_json(out)
    if d and d.get("key") == key and not force:
        return d, True
    tries = []
    if device in ("auto", "cuda"):
        tries.append(("cuda", "float16"))
    if device in ("auto", "cpu"):
        tries.append(("cpu", "int8"))
    _add_cuda_dll_dirs()
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    audio = _audio(path)
    err = None
    for dev, comp in tries:
        try:
            words, detected = _run(path, model, lang, dev, comp, audio)
            break
        except Exception as e:  # missing CUDA libs, OOM, ... -> next option
            err = e
    else:
        raise VtError(f"transcription failed: {err}")
    d = {"key": key, "model": model, "lang": lang or detected, "device": dev, "words": words}
    write_json(out, d)
    return d, False
