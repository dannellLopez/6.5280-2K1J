import numpy as np

from cloudpac.sources import make_source
from cloudpac.sources import stub_pacman as sp
from cloudpac.sources.stub_pacman import StubPacman


def test_all_dots_reachable():
    seen, stack = {sp.PAC_START}, [sp.PAC_START]
    while stack:
        r, c = stack.pop()
        for dr, dc in sp.DELTAS.values():
            n = (r + dr, c + dc)
            if n not in seen and sp._open(*n):
                seen.add(n)
                stack.append(n)
    dots = {divmod(i, sp.COLS) for i, d in enumerate(sp.DOT_TEMPLATE) if d}
    assert dots <= seen


def test_frame_shape_and_dtype():
    g = StubPacman()
    f = g.render()
    assert f.shape == (g.height, g.width, 3) and f.dtype == np.uint8


def _run(g, inputs):
    frames = []
    for held in inputs:
        g.step(held)
        frames.append(g.render().tobytes())
    return frames


def test_controls_catalog():
    catalog = StubPacman().controls()
    assert catalog.mode == "select_one"
    assert [b.id for b in catalog.buttons] == [sp.BTN_UP, sp.BTN_DOWN, sp.BTN_LEFT, sp.BTN_RIGHT]
    assert catalog.to_json()["buttons"][2]["label"] == "Left"


def test_deterministic_and_snapshot_restore():
    inputs = [{sp.BTN_LEFT}, set(), {sp.BTN_UP}, {sp.BTN_RIGHT}, set()] * 60
    a, b = StubPacman(), StubPacman()
    assert _run(a, inputs) == _run(b, inputs)

    g = StubPacman()
    _run(g, inputs[:100])
    snap = g.snapshot()
    first = _run(g, inputs[100:200])
    g.restore(snap)
    assert _run(g, inputs[100:200]) == first


def test_pacman_moves_and_scores():
    g = StubPacman()
    for _ in range(sp.FREEZE_TICKS + 1):
        g.step(set())
    before = g.render().tobytes()
    for _ in range(40):
        g.step({sp.BTN_LEFT})
    assert g.render().tobytes() != before
    assert g._s.score > 0


def test_survives_long_random_play():
    g = StubPacman()
    rng = np.random.default_rng(0)
    acts = [{sp.BTN_UP}, {sp.BTN_DOWN}, {sp.BTN_LEFT}, {sp.BTN_RIGHT}]
    for _ in range(5000):
        g.step(acts[rng.integers(4)])
    assert g.render().shape[2] == 3


def test_make_source():
    assert isinstance(make_source("stub"), StubPacman)
