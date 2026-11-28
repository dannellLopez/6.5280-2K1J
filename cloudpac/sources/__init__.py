from __future__ import annotations

from .base import FrameSource


def make_source(name: str, **kwargs) -> FrameSource:
    """Create a frame source by name. Imports are lazy so optional deps stay optional."""
    if name == "stub":
        from .stub_pacman import StubPacman

        return StubPacman(**kwargs)
    if name == "mame":
        from .mame import MameFrameSource

        return MameFrameSource(**kwargs)
    raise ValueError(f"unknown frame source {name!r}; available: stub, mame")


__all__ = ["FrameSource", "make_source"]
