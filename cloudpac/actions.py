"""Discrete player actions shared by client, server and game sources."""

from enum import IntEnum


class Action(IntEnum):
    NONE = 0
    UP = 1
    DOWN = 2
    LEFT = 3
    RIGHT = 4


def parse_action(name: str) -> Action:
    try:
        return Action[name.strip().upper()]
    except KeyError as exc:
        raise ValueError(f"unknown action {name!r}; expected one of {[a.name for a in Action]}") from exc
