# 6.5280-2K1J — Cloud-gaming networks project

A server hosts a game (simplified to pacman), captures a frame every tick, encodes it and
streams it over the network. A thin client on the player's machine displays the
frames and sends button presses back. We aim to also implement speculative rendering, deadline-aware
frame scheduling and adaptive pacing/bitrate are **not** implemented yet.

![stub frame](docs/stub-frame.png)

## Quick start

Requires Python 3.10+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # numpy, Pillow, pygame, pytest
```

**Server** (machine that hosts the game; works with no ROM and no emulator):

```bash
python -m cloudpac.server --host 0.0.0.0 --port 5280
```

**Client** (your local machine; opens a window):

```bash
python -m cloudpac.client --host <SERVER_IP_OR_HOSTNAME> --port 5280
```

The window shows the streamed frames and one button for each control the server
advertises. Pac-Man advertises four: Up, Down, Left, Right. Click a button, or
use its keys (arrows or WASD). Esc quits. A different game can advertise a
different number of buttons; the client builds the bar from that catalog.

The server address is the only thing you change to play against a remote
machine. Open TCP port `5280` in the server's firewall / cloud security group.

**Both on this machine** (a server process and a client process):

```bash
python -m cloudpac.play
```

Opens a fullscreen window on the device that the streamed frame as large as
the screen allows, and the buttons in a strip across the bottom. Esc closes
it and stops the local server. The same two processes with no window:

```bash
python -m cloudpac.play --headless --duration 5 --random-inputs
```

The client needs `numpy`, `Pillow` and `pygame`; the server only `numpy` and `Pillow`.

### Headless client, traces and tests

```bash
# no window; random inputs for 10 s; prints fps / bandwidth / input->frame latency
python -m cloudpac.client --host <SERVER_IP> --headless --duration 10 --random-inputs --screenshot last.png
# record a session's inputs, replay them later
python -m cloudpac.client --host <SERVER_IP> --record traces/my-run.jsonl
python -m cloudpac.client --host <SERVER_IP> --headless --replay traces/example.jsonl
pytest
```

Server flags: `--fps` (default 30), `--quality` (JPEG, default 80), `--source stub|mame`,
`--rom-dir roms`, `--deadline-ms`. Client flags: `--scale`, `--headless`, `--duration`,
`--replay`, `--random-inputs`, `--record`, `--screenshot`.

## Current Architecture

```
 client machine                                     server machine
┌────────────────────────┐  INPUT (seq, button ids) ┌──────────────────────────────┐
│ buttons / keys / replay│ ───────────────────────▶│ session: latest button set  │
│                        │   FEEDBACK (rx stats)   │   │                          │
│ decode JPEG, display   │ ───────────────────────▶│   ▼                          │
│ measure input→frame ms │◀─────────────────────── │ FrameSource.step → render    │
└────────────────────────┘   FRAME (tags + JPEG)   │   → JPEG encode → send/drop  │
                                                   └──────────────────────────────┘
```


### Codebase layout for Cloudpac

- `cloudpac/sources/base.py` — `FrameSource`: `controls / reset / step(held button ids) / render / snapshot / restore`. The only thing the server knows about the game. Speculation will use `snapshot()` → `step(guessed inputs)` → `render()` → `restore()`.
- `cloudpac/sources/stub_pacman.py` — default, ROM-free Pac-Man-like game (own maze/sprites, 2 ghosts, lives, score).
- `cloudpac/sources/mame.py` — placeholder for an emulator-backed source; raises `NotImplementedError`.
- `cloudpac/protocol.py` — wire format (length-prefixed binary messages over one TCP connection; documented at the top of the file). INPUT carries a set of button ids. HELLO carries the control catalog. Unknown keys, unknown message types, and trailing payload bytes are ignored.
- `cloudpac/controls.py` — the catalog: N buttons, `select_one` or `hold_many`.
- `cloudpac/ui.py` — button layout and press/release rules, with no pygame import.
- `cloudpac/server.py` / `cloudpac/client.py` — as described above; one independent game session per client connection. The client draws the catalog; it does not hardcode four directions.
- `cloudpac/play.py` — start a local server process and a client process against it.