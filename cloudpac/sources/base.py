"""The one interface the server needs from "something that runs a game".

Anything that can produce frames from inputs (a fake game, MAME, a libretro
core) implements `FrameSource`. The speculative-rendering work in the spec
needs `snapshot()` / `restore()`: fork the game state, apply a *guessed* input
sequence, render, then roll back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from ..actions import Action


class FrameSource(ABC):
    #: frame size in pixels; must be constant for a given source
    width: int
    height: int
    #: number of `step()` calls since `reset()`; the "game tick"
    tick: int

    @abstractmethod
    def reset(self) -> None:
        """Start a fresh game."""

    @abstractmethod
    def step(self, action: Action) -> None:
        """Advance the game by exactly one tick with `action` as the held input."""

    @abstractmethod
    def render(self) -> np.ndarray:
        """Return the current frame as an (height, width, 3) uint8 RGB array."""

    @abstractmethod
    def snapshot(self) -> Any:
        """Return an opaque, self-contained copy of the full game state."""

    @abstractmethod
    def restore(self, state: Any) -> None:
        """Restore a state previously returned by `snapshot()`."""

    def close(self) -> None:
        """Release external resources (emulator process, etc.)."""
