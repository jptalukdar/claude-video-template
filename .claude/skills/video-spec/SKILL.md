---
name: video-spec
description: Author or revise a vt video-edit spec (word-timed overlays, lower thirds, cutaways, intro/outro cards, watermarks) and render it. Use when the user asks to edit, caption, brand, preview or render a video project, or to list/render projects in bulk.
---

# vt video spec

All work goes through the `vt` CLI (if not on PATH: `.venv/Scripts/vt` or `python -m vt`). Scripts do anchor resolution, validation and rendering. Never read `.vt/*`, `words.json`, `assets.yaml` or project media. Never rewrite `spec.yaml` whole; change it only with `vt patch`.

## Start
`vt ls` → `vt use NAME` → `vt status`. New project: `vt new NAME path/video.mp4 --assets path/dir` (copies files, transcribes).
Names: `vt assets` (ids), `vt presets` (anims/styles). Use ids and preset names only.

## Find timing (don't read the whole transcript)
- `vt find "phrase"` → `S12#1 1:23.40 …[phrase]…`. Latin queries also match Devanagari/Bengali words (fuzzy, shown `~0.81`).
- `vt transcript -r S10-S20 [--latin]` for a slice; `--latin` romanises (cheaper).

## Anchors (`at:` = phrase start, `to:` = phrase end)
`S12` sentence start · `S12.end` · `S12:word` · `S12:'two words'` · `word#2` (2nd hit) · `'phrase'` (whole transcript) · `word.start`/`.end` (override edge) · `e3.end` (other item) · `@1:23.4` (absolute) · `start`/`end` (kept video) · suffix `+0.5`/`-0.2` · `to: next` (until next item in same `slot`).
Times are in the original video. Trim and intro offsets are applied automatically.

## Patch ops (one `vt patch` call; separate ops with `;`)
```
add ID at=S4:loan text="Loan" sub=Line2 style=lower3 pos=bl anim=rise slot=l3 to=next
add ID at="'free tier'#2" img=chart w=0.4 pos=0.72,0.48 anim=zoom_in dur=3
add ID at=S9 to=S10 img=broll fit=cover anim=quick                # full-frame cutaway
add ID at=S10 to=end frame="{x: -0.19, scale: 1, bg: '#1A37AC'}" tr=0.6   # move base video
add persist text="*T&C apply" style=fine pos=br                    # whole video (scope=all incl. cards)
add intro img=thumb dur=1.1 tr=fade ; add outro clip=cta ; add outro text="Disclaimer…" dur=2
set ID off=-0.2 layer=20 ; set defaults anim=fade ; set trim tail=S6-0.35 ; set spec offset=0.1
unset ID dur ; del ID ; mv ID 0 ; vt undo
```
Item keys: `text sub img clip frame at to dur off style font color size anim pos w h scale fit op layer slot stack scope audio vol`. Card keys: `img clip text dur anim tr trd fit bg audio`.
Leave out values that equal the defaults. Fonts are assets: `font=<id>`.

## Check → preview → render
- Patch output lists errors, warnings for touched items, and `id start-end Llayer kind pos anim` (output seconds).
- `W id: 'w' x3 in S12, used #1` → add `#n`. `W id: ~'…' (0.55)` → a fuzzy match; check the time is plausible.
- Drift: `set spec offset=±s` (global) or `set ID off=±s`.
- `vt preview --around ID` (or `-r S10-S14`) renders that window only. `vt frame ID [--at start|mid|end|0.3]` writes a PNG you can view.
- `vt render`: the final file matches the source's tech spec by default (prints `tech spec = source` or the DIFFs). Override with `set out crf=18 size=1920x1080 fps=30`.
- Bulk: `vt render --all|--glob 'promo-*'|A B [--stale] [-j 2]`, `vt check --all`, `vt transcribe --all`.
