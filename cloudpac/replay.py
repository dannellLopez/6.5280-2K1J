"""Input traces: JSON-lines files of {"t": seconds_since_start, "ids": [2]}.

Used to record a play session and replay the same button presses later, which
the spec's evaluation (replayed inputs under controlled network conditions)
needs. Lines with the older {"action": "LEFT"} form still load: those names
map onto the stub Pac-Man button ids.
"""

from __future__ import annotations

import json
from pathlib import Path

# Names from the original Action enum, in stub button-id order.
_LEGACY_ACTIONS = {
    "NONE": (),
    "UP": (0,),
    "DOWN": (1,),
    "LEFT": (2,),
    "RIGHT": (3,),
}


def load_trace(path: str | Path) -> list[tuple[float, tuple[int, ...]]]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                obj = json.loads(line)
                events.append((float(obj["t"]), _buttons(obj)))
    events.sort(key=lambda e: e[0])
    return events


def _buttons(obj: dict) -> tuple[int, ...]:
    if "ids" in obj:
        return tuple(int(i) for i in obj["ids"])
    if "action" in obj:
        name = str(obj["action"]).strip().upper()
        if name not in _LEGACY_ACTIONS:
            raise ValueError(f"unknown action {name!r}; expected one of {sorted(_LEGACY_ACTIONS)}")
        return _LEGACY_ACTIONS[name]
    raise ValueError("trace event needs 'ids' or 'action'")


def save_trace(path: str | Path, events: list[tuple[float, tuple[int, ...]]]) -> None:
    with open(path, "w") as f:
        for t, ids in events:
            f.write(json.dumps({"t": round(t, 4), "ids": list(ids)}) + "\n")
