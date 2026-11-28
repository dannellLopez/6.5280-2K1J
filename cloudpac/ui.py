"""Client control panel that does not know which game it is showing.

The server sends a Controls catalog. This module turns it into button
rectangles and into the set of ids that should be on the wire. Pygame is not
imported here so the layout and the press/release rules can be tested without
a window.
"""

from __future__ import annotations

from dataclasses import dataclass

from .controls import SELECT_ONE, Button, Controls


@dataclass(frozen=True)
class PlacedButton:
    id: int
    label: str
    keys: tuple[str, ...]
    x: int
    y: int
    w: int
    h: int

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def hint(self) -> str:
        for key in self.keys:
            if len(key) == 1:
                return key.upper()
        return self.keys[0].upper() if self.keys else ""


def layout_buttons(
    buttons: tuple[Button, ...] | list[Button],
    *,
    x: int,
    y: int,
    width: int,
    text_widths: list[int] | tuple[int, ...],
    pad: int = 10,
    gap: int = 8,
    btn_h: int = 42,
    min_w: int = 76,
    text_pad: int = 28,
) -> list[PlacedButton]:
    """Pack buttons into rows under the frame and stretch each row to `width`."""
    if len(buttons) != len(text_widths):
        raise ValueError("text_widths must match buttons")
    inner = max(1, width - 2 * pad)
    desired = [min(inner, max(min_w, tw + text_pad, 1)) for tw in text_widths]

    rows: list[list[tuple[Button, int]]] = []
    current: list[tuple[Button, int]] = []
    used = 0
    for button, bw in zip(buttons, desired):
        extra = bw if not current else gap + bw
        if current and used + extra > inner:
            rows.append(current)
            current = [(button, bw)]
            used = bw
        else:
            current.append((button, bw))
            used += extra
    if current:
        rows.append(current)

    placed: list[PlacedButton] = []
    y_cursor = y + pad
    for row in rows:
        row_used = sum(bw for _, bw in row) + gap * (len(row) - 1)
        extra_px = max(0, inner - row_used)
        base, rem = divmod(extra_px, len(row))
        x_cursor = x + pad
        for i, (button, bw) in enumerate(row):
            w = bw + base + (1 if i < rem else 0)
            placed.append(PlacedButton(button.id, button.label, button.keys, x_cursor, y_cursor, w, btn_h))
            x_cursor += w + gap
        y_cursor += btn_h + gap
    return placed


@dataclass(frozen=True)
class WindowLayout:
    """The game screen and the button bar in one window.

    `view_*` is the rectangle the received frame is drawn into. `bezel_*` is
    the frame around that screen. Buttons start below the bezel.
    """

    scale: int
    width: int
    height: int
    view_x: int
    view_y: int
    view_w: int
    view_h: int
    bezel_x: int
    bezel_y: int
    bezel_w: int
    bezel_h: int
    buttons: tuple[PlacedButton, ...]


def layout_window(
    buttons: tuple[Button, ...] | list[Button],
    text_widths: list[int] | tuple[int, ...],
    src_w: int,
    src_h: int,
    *,
    max_w: int,
    max_h: int,
    requested_scale: int = 3,
    margin: int = 16,
    bezel: int = 10,
) -> WindowLayout:
    """Fit the game screen on the display, then place the buttons under it.

    Scale starts at `requested_scale` and steps down until the window fits
    `max_w` by `max_h`. It never goes below 1.
    """
    if requested_scale < 1 or src_w < 1 or src_h < 1:
        raise ValueError("scale and frame size must be positive")
    scale = requested_scale
    while True:
        view_w, view_h = src_w * scale, src_h * scale
        width = margin + bezel + view_w + bezel + margin
        view_x = margin + bezel
        view_y = margin + bezel
        buttons_y = view_y + view_h + bezel
        placed = layout_buttons(buttons, x=0, y=buttons_y, width=width, text_widths=text_widths)
        height = buttons_y + chrome_height(placed, origin_y=buttons_y)
        if scale == 1 or (width <= max_w and height <= max_h):
            return WindowLayout(
                scale, width, height, view_x, view_y, view_w, view_h,
                margin, margin, width - 2 * margin, bezel + view_h + bezel,
                tuple(placed),
            )
        scale -= 1


def chrome_height(placed: list[PlacedButton], *, origin_y: int, pad: int = 10, status_h: int = 22) -> int:
    """Height of the button bar plus the status line, starting at origin_y."""
    if not placed:
        return pad + status_h + pad
    bottom = max(button.y + button.h for button in placed)
    return (bottom - origin_y) + pad + status_h + pad


def key_index(controls: Controls) -> dict[str, int]:
    """Map a key name to a button id. The first button to claim a key keeps it."""
    index: dict[str, int] = {}
    for button in controls.buttons:
        for key in button.keys:
            index.setdefault(key.lower(), button.id)
    return index


class ControlState:
    """Which buttons are active, and the set to send when that changes.

    Pointer and keyboard are tracked separately so releasing a key does not
    release a button the mouse is still holding (and the reverse).
    `select_one` keeps the last press selected after the key or mouse goes up.
    """

    def __init__(self, controls: Controls) -> None:
        self.mode = controls.mode
        self._valid = {button.id for button in controls.buttons}
        self._pointer: set[int] = set()
        self._keyboard: set[int] = set()
        self.held: frozenset[int] = frozenset()

    def _publish(self) -> tuple[int, ...] | None:
        new = frozenset(self._pointer | self._keyboard)
        if new == self.held:
            return None
        self.held = new
        return tuple(sorted(new))

    def pointer_down(self, button_id: int) -> tuple[int, ...] | None:
        if button_id not in self._valid:
            return None
        if self.mode == SELECT_ONE:
            self._pointer = {button_id}
            self._keyboard = set()
        else:
            self._pointer.add(button_id)
        return self._publish()

    def pointer_up(self, button_id: int) -> tuple[int, ...] | None:
        if self.mode == SELECT_ONE:
            return None
        self._pointer.discard(button_id)
        return self._publish()

    def key_down(self, button_id: int) -> tuple[int, ...] | None:
        if button_id not in self._valid:
            return None
        if self.mode == SELECT_ONE:
            self._keyboard = {button_id}
            self._pointer = set()
        else:
            self._keyboard.add(button_id)
        return self._publish()

    def key_up(self, button_id: int) -> tuple[int, ...] | None:
        if self.mode == SELECT_ONE:
            return None
        self._keyboard.discard(button_id)
        return self._publish()
