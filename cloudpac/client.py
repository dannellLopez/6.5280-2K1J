"""Thin client: connects to a (possibly remote) server, displays the streamed
frames, and sends keyboard input back.

    python -m cloudpac.client --host SERVER_IP --port 5280          # window
    python -m cloudpac.client --host SERVER_IP --headless --duration 10 --random-inputs

Controls (window mode): arrow keys or WASD to steer, Esc to quit.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import random
import socket
import statistics
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from . import DEFAULT_PORT
from .actions import Action
from .codec import decode_frame
from .protocol import (
    MSG_BYE,
    MSG_FRAME,
    MSG_HELLO,
    PROTO_VERSION,
    Feedback,
    FrameHeader,
    pack_feedback,
    pack_hello,
    pack_input,
    pack_message,
    read_message,
    unpack_frame,
    unpack_hello,
)
from .replay import load_trace, save_trace

log = logging.getLogger("cloudpac.client")


@dataclass
class Stats:
    frames: int = 0
    bytes: int = 0
    started: float = field(default_factory=time.monotonic)
    latencies_ms: list = field(default_factory=list)

    def summary(self) -> dict:
        elapsed = max(time.monotonic() - self.started, 1e-9)
        out = {
            "frames": self.frames,
            "seconds": round(elapsed, 2),
            "fps": round(self.frames / elapsed, 1),
            "kbit_per_s": round(self.bytes * 8 / 1000 / elapsed, 1),
            "inputs_measured": len(self.latencies_ms),
        }
        if self.latencies_ms:
            lat = sorted(self.latencies_ms)
            out["input_to_frame_ms"] = {
                "p50": round(statistics.median(lat), 1),
                "p95": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1),
                "max": round(lat[-1], 1),
            }
        return out


class StreamClient:
    """Network side of the client. All coroutines run on one asyncio loop;
    `send_input_threadsafe` may be called from any thread (e.g. a GUI thread)."""

    def __init__(self, host: str, port: int = DEFAULT_PORT) -> None:
        self.host, self.port = host, port
        self.params: dict = {}
        self.latest: tuple[FrameHeader, np.ndarray] | None = None
        self.stats = Stats()
        self.recorded_inputs: list[tuple[float, Action]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader: asyncio.StreamReader | None = None
        self._seq = 0
        self._pending: dict[int, float] = {}
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self.closed: asyncio.Event | None = None  # created in connect()

    async def connect(self, timeout: float = 10.0) -> dict:
        self._loop = asyncio.get_running_loop()
        self.closed = asyncio.Event()
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise ConnectionError(
                f"could not connect to {self.host}:{self.port} ({exc}). Is the server running, "
                "and is the port open in its firewall / security group?"
            ) from exc
        sock = self._writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._writer.write(pack_hello(client="cloudpac-client"))
        msg_type, payload = await asyncio.wait_for(read_message(self._reader), timeout)
        if msg_type != MSG_HELLO:
            raise ConnectionError("server did not send HELLO")
        self.params = unpack_hello(payload)
        if self.params.get("proto") != PROTO_VERSION:
            raise ConnectionError(f"protocol mismatch: server={self.params.get('proto')} client={PROTO_VERSION}")
        self.stats = Stats()
        self._t0 = time.monotonic()
        return self.params

    async def run(self) -> None:
        """Receive frames until the connection closes."""
        assert self._reader is not None and self._writer is not None
        fb_task = asyncio.create_task(self._feedback_loop())
        try:
            while True:
                msg_type, payload = await read_message(self._reader)
                if msg_type == MSG_FRAME:
                    self._on_frame(*unpack_frame(payload), nbytes=len(payload))
                elif msg_type == MSG_BYE:
                    break
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            fb_task.cancel()
            assert self.closed is not None
            self.closed.set()

    def _on_frame(self, header: FrameHeader, image: bytes, nbytes: int) -> None:
        now = time.monotonic()
        rgb = decode_frame(image)
        self.latest = (header, rgb)
        self.stats.frames += 1
        self.stats.bytes += nbytes
        with self._lock:
            done = [s for s in self._pending if s <= header.input_seq]
            for s in done:
                self.stats.latencies_ms.append((now - self._pending.pop(s)) * 1000)

    async def _feedback_loop(self, interval: float = 1.0) -> None:
        last_frames, last_bytes, last_t = 0, 0, time.monotonic()
        while True:
            await asyncio.sleep(interval)
            now = time.monotonic()
            tick = self.latest[0].tick if self.latest else 0
            fb = Feedback(
                tick, self.stats.frames - last_frames, self.stats.bytes - last_bytes, int((now - last_t) * 1000)
            )
            last_frames, last_bytes, last_t = self.stats.frames, self.stats.bytes, now
            assert self._writer is not None
            self._writer.write(pack_feedback(fb))

    def send_input(self, action: Action, stamp: float | None = None) -> None:
        """Send an input. Must run on the client's event loop."""
        assert self._writer is not None
        with self._lock:
            self._seq += 1
            seq = self._seq
            t = stamp if stamp is not None else time.monotonic()
            self._pending[seq] = t
        self.recorded_inputs.append((t - self._t0, action))
        self._writer.write(pack_input(seq, action, time.time()))

    def send_input_threadsafe(self, action: Action) -> None:
        assert self._loop is not None
        stamp = time.monotonic()
        self._loop.call_soon_threadsafe(self.send_input, action, stamp)

    async def close(self) -> None:
        if self._writer is None:
            return
        with contextlib.suppress(Exception):
            self._writer.write(pack_message(MSG_BYE))
            await self._writer.drain()
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()


async def _drive_inputs(client: StreamClient, args: argparse.Namespace) -> None:
    start = time.monotonic()
    if args.replay:
        for t, action in load_trace(args.replay):
            await asyncio.sleep(max(0.0, start + t - time.monotonic()))
            client.send_input(action)
    elif args.random_inputs:
        rng = random.Random(args.seed)
        while True:
            client.send_input(rng.choice([Action.UP, Action.DOWN, Action.LEFT, Action.RIGHT]))
            await asyncio.sleep(0.4)


async def run_headless(args: argparse.Namespace) -> dict:
    client = StreamClient(args.host, args.port)
    params = await client.connect()
    print(f"connected: {params}")
    run_task = asyncio.create_task(client.run())
    driver = asyncio.create_task(_drive_inputs(client, args))
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(run_task), timeout=args.duration)
    driver.cancel()
    summary = client.stats.summary()
    if args.screenshot and client.latest:
        from PIL import Image

        Image.fromarray(client.latest[1]).save(args.screenshot)
        print(f"saved last frame to {args.screenshot}")
    if args.record:
        save_trace(args.record, client.recorded_inputs)
    await client.close()
    run_task.cancel()
    return summary


KEYMAP_NAMES = {
    "up": Action.UP, "w": Action.UP,
    "down": Action.DOWN, "s": Action.DOWN,
    "left": Action.LEFT, "a": Action.LEFT,
    "right": Action.RIGHT, "d": Action.RIGHT,
}  # fmt: skip


def run_gui(args: argparse.Namespace) -> dict:
    try:
        import pygame
    except ImportError:
        raise SystemExit("pygame is required for the window client: pip install pygame (or use --headless)")

    client = StreamClient(args.host, args.port)
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    error: list[BaseException] = []

    def net_thread() -> None:
        asyncio.set_event_loop(loop)

        async def main() -> None:
            try:
                await client.connect()
            except BaseException as exc:  # noqa: BLE001
                error.append(exc)
                ready.set()
                return
            ready.set()
            await client.run()

        loop.run_until_complete(main())

    thread = threading.Thread(target=net_thread, daemon=True)
    thread.start()
    ready.wait()
    if error:
        raise SystemExit(str(error[0]))

    w, h = client.params["width"], client.params["height"]
    scale = args.scale
    pygame.init()
    screen = pygame.display.set_mode((w * scale, h * scale))
    pygame.display.set_caption(f"cloudpac - {args.host}:{args.port}")
    clock = pygame.time.Clock()
    last_tick = -1
    running = True
    try:
        while running and not client.closed.is_set():
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    running = False
                elif event.type == pygame.KEYDOWN:
                    action = KEYMAP_NAMES.get(pygame.key.name(event.key))
                    if action:
                        client.send_input_threadsafe(action)
            latest = client.latest
            if latest and latest[0].tick != last_tick:
                last_tick = latest[0].tick
                surf = pygame.image.frombuffer(np.ascontiguousarray(latest[1]).tobytes(), (w, h), "RGB")
                screen.blit(pygame.transform.scale(surf, (w * scale, h * scale)), (0, 0))
                pygame.display.flip()
            s = client.stats.latencies_ms
            if s and clock.get_fps() > 0 and last_tick % 30 == 0:
                pygame.display.set_caption(f"cloudpac - last input->frame {s[-1]:.0f} ms")
            clock.tick(120)
    finally:
        pygame.quit()
        fut = asyncio.run_coroutine_threadsafe(client.close(), loop)
        with contextlib.suppress(Exception):
            fut.result(timeout=2)
    if args.record:
        save_trace(args.record, client.recorded_inputs)
    return client.stats.summary()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1", help="server address (IP or hostname)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--scale", type=int, default=3, help="window zoom factor")
    p.add_argument("--headless", action="store_true", help="no window; print stats (for testing)")
    p.add_argument("--duration", type=float, default=10.0, help="headless run time in seconds")
    p.add_argument("--replay", help="headless: replay inputs from a trace file")
    p.add_argument("--random-inputs", action="store_true", help="headless: send random directions")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--record", help="write the inputs sent to this trace file")
    p.add_argument("--screenshot", help="headless: save the last received frame to this PNG")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    try:
        summary = asyncio.run(run_headless(args)) if args.headless else run_gui(args)
    except ConnectionError as exc:
        raise SystemExit(str(exc))
    print("summary:", summary)


if __name__ == "__main__":
    main()
