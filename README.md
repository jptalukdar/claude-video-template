# video-template (`vt`)

Spec-driven video editing. You give `vt` a source video and a compact YAML spec, and it renders the edited video:
- text and image overlays timed to spoken words, with animations
- lower thirds, full-frame cutaways, a "move the video aside" split layout
- persistent watermarks and disclaimers
- intro and outro cards (images, clips or text) with transitions

The spec is written and revised by Claude through the [`video-spec` skill](.claude/skills/video-spec/SKILL.md) using small patches. Claude never reads word timings. Local scripts handle transcription, anchor resolution, validation and rendering.

```
source.mp4 ─ vt new ─► transcribe (faster-whisper, cached) ─► sentences S1..Sn
                                   │
            Claude: vt find / vt transcript -r  ─►  vt patch (anchors like S4:loan, 'free tier'#2)
                                   │
            vt check (resolve + validate) ─► vt preview --around e3 ─► iterate ─► vt render
```

## Setup
Requirements: Python ≥ 3.10 and ffmpeg/ffprobe ≥ 7.1 on PATH.

```bash
python -m venv .venv
```
```bash
.venv/Scripts/pip install -e .[dev]
```
Transcription runs on the CPU (int8) by default. To use an NVIDIA GPU, also run `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`; `vt` picks up their DLLs automatically.

## Projects live outside the code
Git tracks only code. Each project is a self-contained folder:

```
<projects root>/<name>/
  spec.yaml  assets.yaml  media/  images/  fonts/  presets/ (optional overrides)
  .vt/   cache: words, sentences, probe, resolved timeline, text PNGs, patch history
  out/   preview.mp4, final.mp4, frames/
```
The projects root is resolved in this order: `--root`, then `$VT_PROJECTS`, then `projects_root` in `vt.local.toml` (gitignored), then `./projects/` (gitignored).

## Commands
| | |
|---|---|
| `vt new NAME video.mp4 [--assets DIR]` | create a project, import assets, probe and transcribe it; it becomes the active project |
| `vt ls` · `vt use NAME` · `vt status` | list projects (freshness, check state) · select one · one-screen summary |
| `vt assets [--add FILES…] [--id X]` · `vt presets` | asset ids · animation and style names |
| `vt transcribe` · `vt compress` | word-level STT (cached by content, model and language) · sentence split |
| `vt find "phrase"` · `vt transcript -r S3-S9 [--latin]` | search with sentence ids and times · print a slice |
| `vt patch OPS` · `vt undo` | `add`/`set`/`unset`/`del`/`mv` ops; comments in the spec are preserved |
| `vt check` | resolve anchors; print errors, warnings and one line per item |
| `vt preview [--around ID \| -r S3-S5]` · `vt frame ID` | low-res preview (or just a window) · still PNG |
| `vt render` | final render using the source's technical spec |
| bulk | `vt render --all \| --glob 'x-*' \| A B [--stale] [-j N] [--fail-fast]`; also `vt check --all` and `vt transcribe --all` |

## Spec
```yaml
src: main
offset: 0.0                      # global transcript drift (s)
trim: {tail: S6-0.35}            # number | auto (static frames) | anchor = cut point
intro: [{img: thumb, dur: 1.1, tr: fade}]
outro: [{clip: apply_now}, {img: disclaimer, dur: 1.64}]
persist: [{id: tc, text: '*T&C apply', style: fine, pos: br}]
events:
- {id: ams, at: Amsterdam, text: Amsterdam, sub: Netherlands, style: place, pos: bl, slot: place, to: next, anim: rise}
- {id: cut, at: S4, to: S4:bajaj.start-0.2, img: laptop, fit: cover, anim: quick}
- {id: split, at: cut.end, frame: {x: -0.19, bg: '#1A37AC'}, tr: 0.6}
- {id: card1, at: "'personal loan'", img: card1, w: 0.43, pos: '0.72,0.48', slot: card, to: next, anim: zoom_in}
```
- **Anchors**:
  - forms: `S12`, `S12.end`, `S12:word`, `S12:'two words'#2`, `word`, `word.start`, `e3.end`, `@1:23.4`, `start`, `end`, `next`, plus a `±s` suffix
  - every anchor resolves against the original video; trim and intro offsets are applied afterwards
  - repeated words take the first match and warn; `#n` picks another occurrence
  - matching tries exact, then romanised, then fuzzy. Latin phrases match Indic-script transcripts (Hinglish etc.) through transliteration and a phonetic skeleton.
- **Defaults and presets**:
  - presets live in [vt/presets](vt/presets): `defaults.yaml`, `animations.yaml` (fade, cross, rise, slide_*, nudge_*, zoom_in, pop, ken_burns, pan_*…) and `styles.yaml` (caption, title, big, lower3, place, tag, fine, card)
  - a project can override them in `<project>/presets/*.yaml`
  - unspecified values fall back to the defaults
- **Overlaps**:
  - `layer` decides draw order (persist items are on 100 and stay on top)
  - items that share a grid `pos` and overlap in time auto-stack
  - items in the same `slot` replace each other (`to: next`, crossfaded by `anim: cross`)
- **Fonts**: add font files as assets and reference them with `font: <id>`. Missing glyphs are reported.
- **Output spec** defaults to the source exactly: codec, profile, level, size, fps, SAR, pix_fmt, colour tags, timescale, audio rate, layout and bitrate (two-pass).
  - x264 writes *Constrained* Baseline for Baseline sources; that counts as equal.
  - Every inserted asset is conformed to this spec.
  - Override with `out: {crf: 18, size: 1920x1080, fps: 30}`.

## Tests
```bash
.venv/Scripts/python -m pytest -q
```
```bash
.venv/Scripts/python -m pytest -q -m e2e
```
The e2e tests generate their own clips (ffmpeg testsrc + Windows speech synthesis) in a temporary projects root.
