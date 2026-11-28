from cloudpac.controls import HOLD_MANY, SELECT_ONE, Button, Controls
from cloudpac.sources.stub_pacman import PACMAN_CONTROLS
from cloudpac.ui import ControlState, chrome_height, key_index, layout_buttons, layout_window


def _buttons(*ids: int) -> tuple[Button, ...]:
    return tuple(Button(i, f"B{i}") for i in ids)


def test_layout_stretches_one_row():
    placed = layout_buttons(_buttons(0, 1), x=0, y=100, width=300, text_widths=[10, 10])
    assert len(placed) == 2
    assert (placed[0].x, placed[0].y, placed[0].w, placed[0].h) == (10, 110, 136, 42)
    assert (placed[1].x, placed[1].y, placed[1].w) == (154, 110, 136)
    assert placed[0].contains(10, 110)
    assert not placed[0].contains(9, 110)
    assert chrome_height(placed, origin_y=100) == 94


def test_layout_wraps_narrow_window():
    placed = layout_buttons(_buttons(0, 1, 2, 3), x=0, y=0, width=200, text_widths=[10, 10, 10, 10])
    assert [button.y for button in placed] == [10, 10, 60, 60]
    assert chrome_height([], origin_y=0) == 42


def test_select_one_keeps_the_last_button():
    state = ControlState(Controls(SELECT_ONE, _buttons(0, 2)))
    assert state.key_down(2) == (2,)
    assert state.key_up(2) is None
    assert state.held == frozenset({2})
    assert state.pointer_down(0) == (0,)
    assert state.pointer_up(0) is None
    assert state.held == frozenset({0})
    assert state.key_down(0) is None
    assert state.pointer_down(9) is None


def test_hold_many_releases_only_the_source_that_let_go():
    state = ControlState(Controls(HOLD_MANY, _buttons(0, 1)))
    assert state.pointer_down(0) == (0,)
    assert state.key_down(0) is None
    assert state.key_up(0) is None
    assert state.key_down(1) == (0, 1)
    assert state.pointer_up(0) == (1,)
    assert state.key_up(1) == ()
    assert state.held == frozenset()


def test_game_screen_sits_above_the_buttons():
    layout = layout_window(
        PACMAN_CONTROLS.buttons, [20, 20, 20, 20], 228, 276,
        max_w=1200, max_h=1100, requested_scale=3,
    )
    assert layout.scale == 3
    assert (layout.view_w, layout.view_h) == (228 * 3, 276 * 3)
    assert layout.view_y >= layout.bezel_y
    assert layout.bezel_y + layout.bezel_h <= min(button.y for button in layout.buttons)
    assert layout.width <= 1200 and layout.height <= 1100


def test_game_screen_shrinks_to_fit_the_display():
    layout = layout_window(
        PACMAN_CONTROLS.buttons, [20, 20, 20, 20], 228, 276,
        max_w=600, max_h=500, requested_scale=3,
    )
    assert layout.scale == 1
    assert layout.width <= 600 and layout.height <= 500
    assert layout.view_w == 228 and layout.view_h == 276


def test_pacman_keys_map_onto_button_ids():
    index = key_index(PACMAN_CONTROLS)
    assert index["w"] == index["up"] == 0
    assert index["a"] == index["left"] == 2
    assert [button.label for button in PACMAN_CONTROLS.buttons] == ["Up", "Down", "Left", "Right"]
    placed = layout_buttons(
        PACMAN_CONTROLS.buttons, x=0, y=0, width=800, text_widths=[20, 20, 20, 20]
    )
    assert [button.hint() for button in placed] == ["W", "S", "A", "D"]
