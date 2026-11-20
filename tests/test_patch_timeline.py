import pytest

from vt.patch import patch, split_ops, undo
from vt.render.graph import build_graph
from vt.render.profile import build_profile
from vt.timeline import build


def ops(s):
    return split_ops(s)


def test_patch_roundtrip_comments_and_undo(proj):
    patch(proj, ops("add e1 at=S2:plan text=Hi style=title ; add persist img=logo pos=tr w=0.1"))
    txt = proj.spec_path.read_text(encoding="utf-8")
    assert "# my comment" in txt
    assert "- {id: e1, at: S2:plan, text: Hi, style: title}" in txt
    assert "{id: p1, img: logo, pos: tr, w: 0.1}" in txt
    patch(proj, ops("set e1 off=-0.2 frame.x=1 ; unset e1 frame ; set defaults anim=rise"))
    txt = proj.spec_path.read_text(encoding="utf-8")
    assert "off: -0.2" in txt and "frame" not in txt and "defaults: {anim: rise}" in txt
    assert undo(proj)
    assert "off: -0.2" not in proj.spec_path.read_text(encoding="utf-8")


def test_patch_errors_are_atomic(proj):
    before = proj.spec_path.read_text(encoding="utf-8")
    with pytest.raises(Exception):
        patch(proj, ops("add e1 text=a at=S1 ; del nope"))
    assert proj.spec_path.read_text(encoding="utf-8") == before


def test_timeline_offsets_trim_intro_and_slots(proj):
    patch(proj, ops("""
        set trim head=0.5
        add intro img=card dur=2
        add outro text="Bye" dur=1.5 tr=fade trd=0.5
        add a1 at=S2:free text=One style=lower3 pos=bl slot=l3 to=next anim=cross
        add a2 at=S3:free text=Two style=lower3 pos=bl slot=l3 to=next anim=cross
        add b1 at=S2:plan text=Stacked style=caption pos=bl dur=3
        add gone at=S1:welcome text=X dur=0.2
    """))
    tl = build(proj)
    ids = {o.id: o for o in tl.overlays}
    assert tl.head == 0.5 and tl.main_start == pytest.approx(2.0)
    # a1 at 1.72 src -> main 1.22 -> out 3.22 ; ends at a2 start + crossfade overlap (0.35)
    assert ids["a1"].out_a == pytest.approx(3.22)
    assert ids["a1"].b == pytest.approx(ids["a2"].a + 0.35)
    assert ids["a2"].b == pytest.approx(tl.main_dur)            # last in slot -> end of video
    assert ids["b1"].stack_off > 0                               # auto-stacked over a1 (same pos, other slot)
    assert "gone" not in ids                                     # fully inside trimmed head
    assert any(w == "gone" and "dropped" in m for _, w, m in tl.msgs.items)
    out = [s for s in tl.segments if s.id == "o1"][0]
    assert tl.total == pytest.approx(2 + tl.main_dur + 1.5 - 0.5)
    assert out.start == pytest.approx(2 + tl.main_dur - 0.5)


def test_validation_messages(proj):
    patch(proj, ops("add e1 at=S1 text=a anm=fade ; add e2 at=S1 img=nope ; add e3 at=S1 text=x anim=zzz"))
    tl = build(proj)
    errs = [f"{w}: {m}" for _, w, m in tl.msgs.errors]
    assert any("unknown key 'anm'" in e for e in errs)
    assert any("img 'nope' not in assets" in e for e in errs)
    assert any("unknown anim 'zzz'" in e for e in errs)


def test_profile_matches_source(proj):
    tl = build(proj)
    p = tl.prof
    assert (p.W, p.H, p.fps, p.vprofile, p.sr, p.channels) == (640, 360, "25/1", "main", 44100, 2)
    args = p.encode_args()
    assert "-profile:v" in args and "main" in args and "-b:v" in args
    pv = build_profile(tl.probe, None, preview=True, preview_w=320)
    assert (pv.W, pv.H) == (320, 180)
    ov = build_profile(tl.probe, {"size": "1280x720", "crf": 20})
    assert (ov.W, ov.H, ov.crf, ov.vbr) == (1280, 720, 20.0, None)


@pytest.mark.parametrize("anim", ["fade", "rise", "slide_l", "zoom_in", "pop", "ken_burns", "pan_l", "none"])
def test_graph_compiles_each_anim(proj, anim):
    patch(proj, ops(f"add e1 at=S2:plan img=logo anim={anim} dur=2 ; add e2 at=S3 text=Hello anim={anim}"))
    tl = build(proj)
    assert not tl.msgs.errors
    g, v, a = build_graph(tl, proj.cache)
    s = g.script()
    assert f"[{v}]" in s and f"[{a}]" in s and "overlay=" in s
    if anim in ("ken_burns", "pan_l"):
        assert "zoompan" in s
    if anim in ("zoom_in", "pop"):
        assert "eval=frame" in s


def test_warns_when_transcript_changes_after_patch(proj):
    import json
    patch(proj, ops("add e1 at=S2 text=Hi"))
    assert not any(w == "spec" for _, w, _ in build(proj).msgs.warnings)
    d = json.loads((proj.cache / "words.json").read_text(encoding="utf-8"))
    d["key"] = "new"
    (proj.cache / "words.json").write_text(json.dumps(d), encoding="utf-8")
    assert any(w == "spec" and "S-ids" in m for _, w, m in build(proj).msgs.warnings)
    patch(proj, ops("set e1 off=0"))
    assert not any(w == "spec" for _, w, _ in build(proj).msgs.warnings)
