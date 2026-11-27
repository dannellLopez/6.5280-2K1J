"""Input traces: JSON-lines files of {"t": seconds_since_start, "action": "LEFT"}.

Used to record a play session and replay the same inputs later, which the
spec's evaluation (replayed inputs under controlled network conditions) needs.
"""

from __future__ import annotations

import json
from pathlib import Path

from .actions import Action, parse_action


def load_trace(path: str | Path) -> list[tuple[float, Action]]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                obj = json.loads(line)
                events.append((float(obj["t"]), parse_action(obj["action"])))
    events.sort(key=lambda e: e[0])
    return events


def save_trace(path: str | Path, events: list[tuple[float, Action]]) -> None:
    with open(path, "w") as f:
        for t, action in events:
            f.write(json.dumps({"t": round(t, 4), "action": Action(action).name}) + "\n")
