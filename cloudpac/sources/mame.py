"""Placeholder for a real-emulator frame source (NOT implemented yet).

The spec does not name an emulator. See README ("Emulator options") for the
trade-offs. The most promising path for speculative rendering is a libretro
core (e.g. via the `libretro.py` package) because libretro exposes
`retro_serialize` / `retro_unserialize`, which map directly onto
`FrameSource.snapshot()` / `restore()`. A MAME process driven by a Lua
`-autoboot_script` is the alternative, but it has no cheap save/restore.

To implement: subclass `FrameSource`, load the ROM from `roms/` (supplied by
the user, never committed), publish a button catalog from `controls()`, map
those button ids onto joystick inputs, run one emulated frame per `step()`,
and return the screen pixels from `render()`.
"""

from __future__ import annotations

from .base import FrameSource


class MameFrameSource(FrameSource):
    def __init__(self, rom_dir: str = "roms", **_: object) -> None:
        raise NotImplementedError(
            "The emulator-backed source is not implemented yet. Use --source stub, "
            "or see cloudpac/sources/mame.py and the README for how to add one."
        )

    def controls(self):  # pragma: no cover
        raise NotImplementedError

    def reset(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def step(self, held) -> None:  # pragma: no cover
        raise NotImplementedError

    def render(self):  # pragma: no cover
        raise NotImplementedError

    def snapshot(self):  # pragma: no cover
        raise NotImplementedError

    def restore(self, state) -> None:  # pragma: no cover
        raise NotImplementedError
