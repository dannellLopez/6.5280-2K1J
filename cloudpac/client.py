"""Thin client: connects to a (possibly remote) server, displays the streamed
frames, and sends button presses back.

    python -m cloudpac.client --host SERVER_IP --port 5280
    python -m cloudpac.client --host SERVER_IP --headless --duration 10 --random-inputs

The window is the game GUI. It draws whatever buttons the server advertised
in HELLO (Pac-Man advertises four) and sends the active set of button ids.
Esc quits.
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
from .codec import decode_frame
from .controls import Controls
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
from .ui import ControlState, key_index, layout_window

_BG = (14, 14, 18)
_SCREEN = (0, 0, 0)
_BEZEL = (42, 44, 58)
_CHROME = (22, 22, 28)
_BUTTON = (48, 50, 64)
_BUTTON_ON = (255, 204, 64)
_TEXT = (240, 240, 245)
_TEXT_ON = (28, 28, 32)
_HINT = (150, 152, 168)
_HINT_ON = (90, 68, 16)
_STATUS = (176, 176, 188)
_RULE = (58, 60, 74)


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
    `send_buttons_threadsafe` may be called from any thread (e.g. a GUI thread)."""

    def __init__(self, host: str, port: int = DEFAULT_PORT) -> None:
        self.host, self.port = host, port
        self.params: dict = {}
        self.controls = Controls("select_one", ())
        self.latest: tuple[FrameHeader, np.ndarray] | None = None
        self.stats = Stats()
        self.recorded_inputs: list[tuple[float, tuple[int, ...]]] = []
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
        if self.params.get("codec") != "jpeg":
            raise ConnectionError(f"unsupported codec {self.params.get('codec')!r}")
        try:
            self.controls = Controls.from_json(self.params["controls"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConnectionError(f"server HELLO has no usable controls catalog ({exc})") from exc
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
                # Unknown types are ignored so a newer server can add messages.
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            fb_task.cancel()
            assert self.closed is not None
            self.closed.set()

    def _on_frame(self, header: FrameHeader, image: bytes, nbytes: int) -> None:
        now = time.monotonic()
        self.stats.frames += 1
        self.stats.bytes += nbytes
        # Speculative frames are tagged in the protocol. Show only confirmed ones
        # so a later server can send branches without the picture jumping ahead.
        if header.speculative:
            return
        rgb = decode_frame(image)
        self.latest = (header, rgb)
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

    def send_buttons(self, buttons, stamp: float | None = None) -> None:
        """Send the full set of active button ids. Must run on the client's event loop."""
        assert self._writer is not None
        ids = tuple(sorted({int(b) for b in buttons}))
        with self._lock:
            self._seq += 1
            seq = self._seq
            t = stamp if stamp is not None else time.monotonic()
            self._pending[seq] = t
        self.recorded_inputs.append((t - self._t0, ids))
        self._writer.write(pack_input(seq, ids, time.time()))

    def send_buttons_threadsafe(self, buttons) -> None:
        assert self._loop is not None
        ids = tuple(buttons)
        stamp = time.monotonic()
        self._loop.call_soon_threadsafe(self.send_buttons, ids, stamp)

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
        for t, buttons in load_trace(args.replay):
            await asyncio.sleep(max(0.0, start + t - time.monotonic()))
            client.send_buttons(buttons)
    elif args.random_inputs:
        ids = [button.id for button in client.controls.buttons]
        if not ids:
            return
        rng = random.Random(args.seed)
        while True:
            client.send_buttons((rng.choice(ids),))
            await asyncio.sleep(0.4)


async def run_headless(args: argparse.Namespace) -> dict:
    client = StreamClient(args.host, args.port)
    params = await client.connect()
    print(f"connected: {params}")
    run_task = asyncio.create_task(client.run())
    driver = asyncio.create_task(_drive_inputs(client, args))
    duration = 10.0 if args.duration is None else args.duration
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(run_task), timeout=duration)
    driver.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await driver
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


def _emit(client: StreamClient, buttons: tuple[int, ...] | None) -> None:
    if buttons is not None:
        client.send_buttons_threadsafe(buttons)


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
            driver = None
            try:
                try:
                    await client.connect()
                except BaseException as exc:  # noqa: BLE001
                    error.append(exc)
                    ready.set()
                    return
                ready.set()
                if args.replay or args.random_inputs:
                    driver = asyncio.create_task(_drive_inputs(client, args))
                await client.run()
            finally:
                if driver is not None:
                    driver.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await driver

        loop.run_until_complete(main())

    thread = threading.Thread(target=net_thread, daemon=True)
    thread.start()
    if not ready.wait(timeout=20):
        raise SystemExit(f"timed out connecting to {args.host}:{args.port}")
    if error:
        raise SystemExit(str(error[0]))

    src_w, src_h = client.params["width"], client.params["height"]
    pygame.init()
    screen = None
    font = pygame.font.SysFont("segoeui", 18)
    small = pygame.font.SysFont("segoeui", 14)
    hint_font = pygame.font.SysFont("segoeui", 11)
    info = pygame.display.Info()
    max_w = int((info.current_w or 1280) * 0.96)
    max_h = int((info.current_h or 800) * 0.88)
    text_widths = [font.size(button.label)[0] for button in client.controls.buttons]
    layout = layout_window(
        client.controls.buttons, text_widths, src_w, src_h,
        max_w=max(1, max_w), max_h=max(1, max_h), requested_scale=args.scale,
    )
    placed = layout.buttons
    screen = pygame.display.set_mode((layout.width, layout.height))
    pygame.display.set_caption(f"cloudpac — {client.params.get('game', args.host)}")
    clock = pygame.time.Clock()
    state = ControlState(client.controls)
    keys = key_index(client.controls)
    grabbed: int | None = None
    drawn_tick = -1
    frame_surf = None
    deadline = None if args.duration is None else time.monotonic() + args.duration
    running = True
    try:
        while running and client.closed is not None and not client.closed.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                break
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    running = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    for button in placed:
                        if button.contains(*event.pos):
                            grabbed = button.id
                            _emit(client, state.pointer_down(button.id))
                            break
                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    if grabbed is not None:
                        _emit(client, state.pointer_up(grabbed))
                        grabbed = None
                elif event.type == pygame.KEYDOWN and not getattr(event, "repeat", False):
                    button_id = keys.get(pygame.key.name(event.key))
                    if button_id is not None:
                        _emit(client, state.key_down(button_id))
                elif event.type == pygame.KEYUP:
                    button_id = keys.get(pygame.key.name(event.key))
                    if button_id is not None:
                        _emit(client, state.key_up(button_id))

            screen.fill(_BG)
            pygame.draw.rect(
                screen, _BEZEL, (layout.bezel_x, layout.bezel_y, layout.bezel_w, layout.bezel_h), border_radius=8
            )
            pygame.draw.rect(screen, _SCREEN, (layout.view_x, layout.view_y, layout.view_w, layout.view_h))
            latest = client.latest
            if latest and latest[0].tick != drawn_tick:
                drawn_tick = latest[0].tick
                rgb = np.ascontiguousarray(latest[1])
                raw = rgb.tobytes()
                surf = pygame.image.frombytes(raw, (rgb.shape[1], rgb.shape[0]), "RGB")
                frame_surf = pygame.transform.scale(surf, (layout.view_w, layout.view_h))
            if frame_surf is not None:
                screen.blit(frame_surf, (layout.view_x, layout.view_y))
            else:
                msg = font.render("waiting for frames", True, _STATUS)
                screen.blit(msg, msg.get_rect(center=(layout.view_x + layout.view_w // 2, layout.view_y + layout.view_h // 2)))

            chrome_top = layout.bezel_y + layout.bezel_h
            pygame.draw.rect(screen, _CHROME, (0, chrome_top, layout.width, layout.height - chrome_top))
            pygame.draw.line(screen, _RULE, (0, chrome_top), (layout.width, chrome_top))
            for button in placed:
                active = button.id in state.held
                rect = pygame.Rect(button.x, button.y, button.w, button.h)
                radius = min(8, rect.w // 2, rect.h // 2)
                pygame.draw.rect(screen, _BUTTON_ON if active else _BUTTON, rect, border_radius=radius)
                hint = button.hint()
                label = font.render(button.label, True, _TEXT_ON if active else _TEXT)
                label_rect = label.get_rect(center=rect.center)
                if hint:
                    label_rect.centery -= 6
                screen.blit(label, label_rect)
                if hint:
                    hint_surf = hint_font.render(hint, True, _HINT_ON if active else _HINT)
                    screen.blit(hint_surf, hint_surf.get_rect(bottomright=(rect.right - 8, rect.bottom - 5)))
            elapsed = max(time.monotonic() - client.stats.started, 1e-9)
            fps = client.stats.frames / elapsed
            lat = client.stats.latencies_ms
            lat_s = f"{lat[-1]:.0f} ms" if lat else "--"
            status = small.render(f"{fps:.0f} fps    input->frame {lat_s}", True, _STATUS)
            screen.blit(status, (12, layout.height - 30))
            pygame.display.flip()
            clock.tick(60)
    finally:
        if args.screenshot and screen is not None:
            pygame.image.save(screen, args.screenshot)
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
    p.add_argument("--duration", type=float, default=None, help="stop after this many seconds (headless default: 10)")
    p.add_argument("--replay", help="replay button ids from a trace file")
    p.add_argument("--random-inputs", action="store_true", help="press random buttons from the server catalog")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--record", help="write the buttons sent to this trace file")
    p.add_argument("--screenshot", help="save the last frame (headless) or the window (GUI) to this PNG")
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
