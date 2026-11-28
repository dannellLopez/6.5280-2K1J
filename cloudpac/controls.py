"""Button catalog shared by a frame source and the client.

The wire protocol carries button ids, not game-specific actions. A game
publishes N buttons in the HELLO catalog. The client draws those buttons and
sends the set that is currently active. A new game changes this catalog; it
does not change the message layout.
"""

from __future__ import annotations

from dataclasses import dataclass

# Held buttons in one INPUT, and buttons in one catalog. The wire count is u16,
# but a control panel past this is not a GUI we want to generate.
MAX_BUTTONS = 256

SELECT_ONE = "select_one"  # radio group: the last press stays selected
HOLD_MANY = "hold_many"  # a button is active only while it is held
MODES = (SELECT_ONE, HOLD_MANY)


@dataclass(frozen=True)
class Button:
    id: int
    label: str
    keys: tuple[str, ...] = ()

    def to_json(self) -> dict:
        out: dict = {"id": self.id, "label": self.label}
        if self.keys:
            out["keys"] = list(self.keys)
        return out


@dataclass(frozen=True)
class Controls:
    mode: str
    buttons: tuple[Button, ...]

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown control mode {self.mode!r}; expected one of {MODES}")
        if len(self.buttons) > MAX_BUTTONS:
            raise ValueError(f"too many buttons: {len(self.buttons)}")
        ids = [b.id for b in self.buttons]
        if any(type(i) is not int or i < 0 or i > 0xFFFF for i in ids):
            raise ValueError("button ids must be integers in 0..65535")
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate button ids: {ids}")
        for button in self.buttons:
            if not isinstance(button.label, str) or not button.label:
                raise ValueError(f"button {button.id} needs a label")

    def to_json(self) -> dict:
        return {"mode": self.mode, "buttons": [b.to_json() for b in self.buttons]}

    @staticmethod
    def from_json(data: object) -> Controls:
        if not isinstance(data, dict):
            raise ValueError("controls must be an object")
        raw = data.get("buttons")
        if not isinstance(raw, list):
            raise ValueError("controls.buttons must be a list")
        buttons: list[Button] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError(f"bad button spec: {item!r}")
            if type(item.get("id")) is not int or not isinstance(item.get("label"), str):
                raise ValueError(f"button needs an integer id and a string label: {item!r}")
            keys = item.get("keys", [])
            if keys is None:
                keys = []
            if isinstance(keys, str) or not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                raise ValueError(f"button keys must be a list of strings: {item!r}")
            buttons.append(Button(item["id"], item["label"], tuple(k.lower() for k in keys)))
        return Controls(str(data.get("mode")), tuple(buttons))


def normalize_held(mode: str, valid: set[int], ids) -> frozenset[int]:
    """Keep known ids. In select_one mode a multi-id set collapses to the lowest id."""
    chosen = frozenset(int(i) for i in ids if int(i) in valid)
    if mode == SELECT_ONE and len(chosen) > 1:
        return frozenset((min(chosen),))
    return chosen
