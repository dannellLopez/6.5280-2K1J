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
    """The game screen and the button bar filling one device display.

    `view_*` is the rectangle the received frame is drawn into, as large as
    the area above the buttons allows without stretching the picture.
    `bezel_*` is the frame around that screen. Buttons span the full width.
    """

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


def _fit_frame(src_w: int, src_h: int, box_w: int, box_h: int) -> tuple[int, int]:
    """Largest size with the source aspect ratio that fits in the box."""
    scale = min(box_w / src_w, box_h / src_h)
    return max(1, min(box_w, int(src_w * scale))), max(1, min(box_h, int(src_h * scale)))


def layout_window(
    buttons: tuple[Button, ...] | list[Button],
    text_widths: list[int] | tuple[int, ...],
    src_w: int,
    src_h: int,
    *,
    max_w: int,
    max_h: int,
    margin: int = 20,
    bezel: int = 12,
    btn_h: int | None = None,
    status_h: int = 28,
    pad: int = 12,
    gap: int = 10,
) -> WindowLayout:
    """Fill the device. Buttons take a full-width strip; the game fills the rest.

    The picture keeps its aspect ratio, so a tall game on a wide screen has
    bars on the sides instead of a stretched maze.
    """
    if max_w < 1 or max_h < 1 or src_w < 1 or src_h < 1:
        raise ValueError("screen and frame size must be positive")
    if btn_h is None:
        btn_h = max(56, min(120, max_h // 8))
    width, height = max_w, max_h
    draft = layout_buttons(
        buttons, x=0, y=0, width=width, text_widths=text_widths, pad=pad, gap=gap, btn_h=btn_h
    )
    chrome = chrome_height(draft, origin_y=0, pad=pad, status_h=status_h)
    buttons_y = max(0, height - chrome)
    placed = tuple(layout_buttons(
        buttons, x=0, y=buttons_y, width=width, text_widths=text_widths, pad=pad, gap=gap, btn_h=btn_h
    ))
    region_w = max(1, width - 2 * margin)
    region_bottom = buttons_y if placed else height - margin
    region_h = max(1, region_bottom - margin - bezel)
    view_w, view_h = _fit_frame(src_w, src_h, region_w, region_h)
    view_x = margin + (region_w - view_w) // 2
    view_y = margin + max(0, (region_h - view_h) // 2)
    bezel_x = max(0, view_x - bezel)
    bezel_y = max(0, view_y - bezel)
    bezel_r = min(width, view_x + view_w + bezel)
    bezel_b = min(height, view_y + view_h + bezel)
    return WindowLayout(
        width, height, view_x, view_y, view_w, view_h,
        bezel_x, bezel_y, bezel_r - bezel_x, bezel_b - bezel_y,
        placed,
    )


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
