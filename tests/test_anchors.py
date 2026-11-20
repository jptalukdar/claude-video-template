import pytest

from tests.conftest import WORDS
from vt.anchors import Resolver, parse_anchor
from vt.spec import Msgs
from vt.transcript import Transcript, compress


@pytest.fixture
def tr():
    return Transcript(WORDS, compress(WORDS))


def R(tr, **kw):
    return Resolver(tr, kw.pop("offset", 0.0), (0.0, 6.0), **kw)


@pytest.mark.parametrize("raw,kind,extra", [
    ("@1:02.5", "abs", {"t": 62.5}),
    (12.5, "abs", {"t": 12.5}),
    ("S3", "sent", {"sid": 3}),
    ("S3.end", "sent", {"edge": "end"}),
    ("S2:free", "phrase", {"sid": 2, "phrase": "free"}),
    ("S2:'free plan'#1", "phrase", {"phrase": "free plan", "occ": 1}),
    ("free#2", "phrase", {"phrase": "free", "occ": 2}),
    ("e1.end+0.5", "ref", {"ref": "e1", "edge": "end", "off": 0.5}),
    ("S4:bajaj.start-0.2", "phrase", {"edge": "start", "off": -0.2}),
    ("S3-0.35", "sent", {"off": -0.35}),
    ("'covid-19'", "phrase", {"phrase": "covid-19", "off": 0.0}),
    ("start", "kw", {"edge": "start"}),
    ("next", "next", {}),
])
def test_parse(raw, kind, extra):
    a = parse_anchor(raw)
    assert a.kind == kind
    for k, v in extra.items():
        assert getattr(a, k) == pytest.approx(v) if isinstance(v, float) else getattr(a, k) == v


def test_sentences(tr):
    assert [s[:2] for s in tr.sents] == [[0, 4], [4, 10], [10, 15], [15, 18]]


def test_repeated_word_in_sentence_warns_first_match(tr):
    m = Msgs()
    assert R(tr).resolve("S2:the", "start", "e1", m) == pytest.approx(1.60)
    assert m.warnings and "x2 in S2" in m.warnings[0][2]
    m = Msgs()
    assert R(tr).resolve("S2:the#2", "start", "e1", m) == pytest.approx(2.52)
    assert not m.items


def test_global_repeat_and_occurrence(tr):
    m = Msgs()
    assert R(tr).resolve("free", "start", "e", m) == pytest.approx(1.72)
    assert "S2,S3" in m.warnings[0][2]
    m = Msgs()
    assert R(tr).resolve("free#2", "start", "e", m) == pytest.approx(3.52)


def test_phrase_edges_and_offsets(tr):
    m = Msgs()
    assert R(tr).resolve("'free tier'", "end", "e", m) == pytest.approx(3.95)
    assert R(tr).resolve("'free tier'.start", "end", "e", m) == pytest.approx(3.52)
    assert R(tr, offset=0.1).resolve("S3:limits+0.5", "start", "e", m) == pytest.approx(4.12 + 0.1 + 0.5)
    assert R(tr, offset=0.1).resolve("@2", "start", "e", m) == pytest.approx(2.0)  # drift not applied


def test_fuzzy_and_cross_script(tr):
    m = Msgs()
    assert R(tr).resolve("limts", "start", "e", m) == pytest.approx(4.12)
    assert m.warnings
    m = Msgs()
    assert R(tr).resolve("Amsterdam", "start", "e", m) == pytest.approx(4.90)
    assert "आमस्तुदाम" in m.warnings[0][2]


def test_not_found_suggests(tr):
    m = Msgs()
    assert R(tr).resolve("zebra crossing", "start", "e", m) is None
    assert m.errors and "not found" in m.errors[0][2]


def test_refs_and_bare_word_edges(tr):
    m = Msgs()
    r = R(tr, ref=lambda oid, edge: 10.0 if edge == "end" else 9.0, known={"e1"})
    assert r.resolve("e1.end+1", "start", "e2", m) == pytest.approx(11.0)
    assert r.resolve("plan.start", "end", "e2", m) == pytest.approx(1.97)  # not an item id -> phrase
