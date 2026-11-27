"""A tiny, fully deterministic Pac-Man-like game used as the default frame source.

Why it exists: it needs no ROM, no emulator and no copyrighted assets (the maze,
sprites and rules here are original and much simpler than the arcade game), yet
it behaves like the real thing from the pipeline's point of view: fixed tick
rate, discrete inputs, RGB frames out, and cheap snapshot/restore for the
speculative rendering experiments described in the spec.

Determinism: all randomness comes from an integer LCG stored in the state, so
the same (state, input sequence) always produces the same frames.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..actions import Action
from .base import FrameSource

MAZE = [
    "###################",
    "#........#........#",
    "#.##.###.#.###.##.#",
    "#.................#",
    "#.##.#.#####.#.##.#",
    "#....#...#...#....#",
    "####.### # ###.####",
    "####.#       #.####",
    "####.#########.####",
    "#.................#",
    "#.##.###.#.###.##.#",
    "#.................#",
    "####.#########.####",
    "####.#       #.####",
    "####.### # ###.####",
    "#....#...#...#....#",
    "#.##.#.#####.#.##.#",
    "#.................#",
    "#.##.###.#.###.##.#",
    "#........#........#",
    "###################",
]
ROWS = len(MAZE)
COLS = len(MAZE[0])
TILE = 12
HUD_H = 24
PAC_START = (11, 9)
GHOST_STARTS = [(7, 8), (7, 10)]
PAC_PERIOD = 4  # ticks per tile moved
GHOST_PERIOD = 5
FREEZE_TICKS = 20  # pause after losing a life / starting a level
GAME_OVER_TICKS = 90
START_LIVES = 3

DELTAS = {
    Action.UP: (-1, 0),
    Action.DOWN: (1, 0),
    Action.LEFT: (0, -1),
    Action.RIGHT: (0, 1),
}
REVERSE = {Action.UP: Action.DOWN, Action.DOWN: Action.UP, Action.LEFT: Action.RIGHT, Action.RIGHT: Action.LEFT}
FACING_DEG = {Action.RIGHT: 0.0, Action.UP: 90.0, Action.LEFT: 180.0, Action.DOWN: 270.0}

WALL = np.array([[ch == "#" for ch in row] for row in MAZE], dtype=bool)
DOT_TEMPLATE = bytes(1 if ch == "." else 0 for row in MAZE for ch in row)

BG = (0, 0, 0)
WALL_COLOR = (33, 33, 222)
DOT_COLOR = (255, 184, 174)
PAC_COLOR = (255, 255, 0)
GHOST_COLORS = [(255, 0, 0), (255, 184, 255)]
TEXT_COLOR = (255, 255, 255)

_DIGITS = {
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111",
    "3": "111001111001111", "4": "101101111001001", "5": "111100111001111",
    "6": "111100111101111", "7": "111001001001001", "8": "111101111101111",
    "9": "111101111001111",
}  # fmt: skip


def _open(r: int, c: int) -> bool:
    return 0 <= r < ROWS and 0 <= c < COLS and not WALL[r, c]


@dataclass
class _State:
    tick: int = 0
    rng: int = 12345
    pac: tuple = PAC_START
    pac_prev: tuple = PAC_START
    pac_dir: Action = Action.NONE
    pac_want: Action = Action.NONE
    ghosts: list = field(default_factory=list)  # [[r, c, prev_r, prev_c, dir], ...]
    dots: bytes = DOT_TEMPLATE
    score: int = 0
    lives: int = START_LIVES
    level: int = 1
    freeze: int = FREEZE_TICKS
    game_over: int = 0  # >0: ticks remaining on the game-over screen


class StubPacman(FrameSource):
    def __init__(self, seed: int = 12345, **_: object) -> None:
        self.seed = seed
        self.width = COLS * TILE
        self.height = HUD_H + ROWS * TILE
        self._s = _State()
        self._background = self._build_background()
        yy, xx = np.mgrid[0:TILE, 0:TILE]
        self._dy = yy - (TILE - 1) / 2
        self._dx = xx - (TILE - 1) / 2
        self._disc = (self._dx**2 + self._dy**2) <= (TILE / 2 - 0.5) ** 2
        self._ghost_mask = self._disc | ((self._dy >= 0) & (np.abs(self._dx) <= TILE / 2 - 0.5))
        self.reset()

    @property
    def tick(self) -> int:  # type: ignore[override]
        return self._s.tick

    def reset(self) -> None:
        self._s = _State(rng=self.seed)
        self._new_round()

    def snapshot(self) -> Any:
        return copy.deepcopy(self._s)

    def restore(self, state: Any) -> None:
        self._s = copy.deepcopy(state)

    def _rand(self, n: int) -> int:
        s = self._s
        s.rng = (s.rng * 1103515245 + 12345) & 0x7FFFFFFF
        return (s.rng >> 16) % n

    def _new_round(self) -> None:
        s = self._s
        s.pac = s.pac_prev = PAC_START
        s.pac_dir = s.pac_want = Action.NONE
        s.ghosts = [[r, c, r, c, Action.NONE] for r, c in GHOST_STARTS]
        s.freeze = FREEZE_TICKS

    def step(self, action: Action) -> None:
        s = self._s
        s.tick += 1
        if s.game_over:
            s.game_over -= 1
            if s.game_over == 0:
                keep_tick, keep_rng = s.tick, s.rng
                self._s = _State(tick=keep_tick, rng=keep_rng)
                self._new_round()
            return
        if action != Action.NONE:
            s.pac_want = Action(action)
        if s.freeze:
            s.freeze -= 1
            return
        if s.tick % PAC_PERIOD == 0:
            self._move_pac()
            if self._hit():
                self._lose_life()
                return
        if s.tick % GHOST_PERIOD == 0:
            self._move_ghosts()
            if self._hit():
                self._lose_life()
                return
        if not any(s.dots):
            s.level += 1
            s.dots = DOT_TEMPLATE
            self._new_round()

    def _move_pac(self) -> None:
        s = self._s
        s.pac_prev = s.pac
        for d in (s.pac_want, s.pac_dir):
            if d == Action.NONE:
                continue
            dr, dc = DELTAS[d]
            nr, nc = s.pac[0] + dr, s.pac[1] + dc
            if _open(nr, nc):
                s.pac, s.pac_dir = (nr, nc), d
                break
        else:
            return
        idx = s.pac[0] * COLS + s.pac[1]
        if s.dots[idx]:
            dots = bytearray(s.dots)
            dots[idx] = 0
            s.dots = bytes(dots)
            s.score += 10

    def _move_ghosts(self) -> None:
        s = self._s
        for g in s.ghosts:
            r, c, _, _, d = g
            options = [a for a, (dr, dc) in DELTAS.items() if _open(r + dr, c + dc)]
            if len(options) > 1 and d != Action.NONE:
                options = [a for a in options if a != REVERSE[d]]
            if self._rand(100) < 60:
                options.sort(key=lambda a: abs(r + DELTAS[a][0] - s.pac[0]) + abs(c + DELTAS[a][1] - s.pac[1]))
                choice = options[0]
            else:
                choice = options[self._rand(len(options))]
            dr, dc = DELTAS[choice]
            g[2], g[3] = r, c
            g[0], g[1] = r + dr, c + dc
            g[4] = choice

    def _hit(self) -> bool:
        s = self._s
        for r, c, pr, pc, _ in s.ghosts:
            if (r, c) == s.pac or ((r, c) == s.pac_prev and (pr, pc) == s.pac):
                return True
        return False

    def _lose_life(self) -> None:
        s = self._s
        s.lives -= 1
        if s.lives <= 0:
            s.game_over = GAME_OVER_TICKS
        self._new_round()

    def _build_background(self) -> np.ndarray:
        img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        img[:] = BG
        for r in range(ROWS):
            for c in range(COLS):
                if WALL[r, c]:
                    y, x = HUD_H + r * TILE, c * TILE
                    img[y + 1 : y + TILE - 1, x + 1 : x + TILE - 1] = WALL_COLOR
        return img

    def _blit(self, img: np.ndarray, mask: np.ndarray, pos: tuple[float, float], color) -> None:
        y = HUD_H + int(round(pos[0] * TILE))
        x = int(round(pos[1] * TILE))
        img[y : y + TILE, x : x + TILE][mask] = color

    def _text(self, img: np.ndarray, text: str, x: int, y: int, scale: int = 2) -> None:
        for ch in text:
            bits = _DIGITS[ch]
            for i, b in enumerate(bits):
                if b == "1":
                    py, px = y + (i // 3) * scale, x + (i % 3) * scale
                    img[py : py + scale, px : px + scale] = TEXT_COLOR
            x += 4 * scale

    def render(self) -> np.ndarray:
        s = self._s
        img = self._background.copy()
        for idx, present in enumerate(s.dots):
            if present:
                r, c = divmod(idx, COLS)
                y, x = HUD_H + r * TILE + TILE // 2 - 1, c * TILE + TILE // 2 - 1
                img[y : y + 2, x : x + 2] = DOT_COLOR

        def interp(cur, prev, period):
            if s.freeze or s.game_over:
                return cur
            f = ((s.tick % period) + 1) / period
            return (prev[0] + (cur[0] - prev[0]) * f, prev[1] + (cur[1] - prev[1]) * f)

        for i, (r, c, pr, pc, _) in enumerate(s.ghosts):
            pos = interp((r, c), (pr, pc), GHOST_PERIOD)
            self._blit(img, self._ghost_mask, pos, GHOST_COLORS[i % len(GHOST_COLORS)])

        if not s.game_over:
            facing = FACING_DEG.get(s.pac_dir if s.pac_dir != Action.NONE else Action.RIGHT)
            mouth = (0.0, 25.0, 50.0, 25.0)[(s.tick // 3) % 4]
            ang = np.degrees(np.arctan2(-self._dy, self._dx))
            diff = np.abs((ang - facing + 180.0) % 360.0 - 180.0)
            mask = self._disc & ~((diff < mouth) & (self._dx**2 + self._dy**2 > 1))
            self._blit(img, mask, interp(s.pac, s.pac_prev, PAC_PERIOD), PAC_COLOR)

        self._text(img, str(s.score), 4, 7)
        for i in range(max(s.lives - 1, 0)):
            x = self.width - (i + 1) * (TILE + 2)
            img[6 : 6 + TILE, x : x + TILE][self._disc] = PAC_COLOR
        if s.game_over:
            img[self.height // 2 - 6 : self.height // 2 + 6, 20 : self.width - 20] = (150, 0, 0)
        return img
