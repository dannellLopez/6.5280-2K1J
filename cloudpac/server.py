"""Game server: hosts a FrameSource, runs it at a fixed tick rate, encodes each
frame and streams it to the connected client; applies the client's inputs.

Each client connection gets its own independent game session.

    python -m cloudpac.server --host 0.0.0.0 --port 5280 --source stub
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import socket
import time
from typing import Callable

from . import DEFAULT_PORT
from .codec import encode_frame
from .controls import normalize_held
from .protocol import (
    MSG_BYE,
    MSG_FEEDBACK,
    MSG_HELLO,
    MSG_INPUT,
    PROTO_VERSION,
    FrameHeader,
    pack_frame,
    pack_hello,
    read_message,
    unpack_feedback,
    unpack_hello,
    unpack_input,
)
from .sources import FrameSource, make_source

log = logging.getLogger("cloudpac.server")

# If this many bytes are still queued to the client, skip sending the new frame
# rather than letting latency build up. A stand-in for the spec's scheduler.
MAX_BACKLOG_BYTES = 256 * 1024


class StreamServer:
    def __init__(
        self,
        source_factory: Callable[[], FrameSource],
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        fps: int = 30,
        quality: int = 80,
        deadline_ms: int = 100,
        game_name: str = "pacman",
    ) -> None:
        self.source_factory = source_factory
        self.host, self.port = host, port
        self.fps, self.quality, self.deadline_ms = fps, quality, deadline_ms
        self.game_name = game_name
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("listening on %s:%d (source factory=%s, %d fps)", self.host, self.port, self.game_name, self.fps)

    async def serve_forever(self) -> None:
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        sock = writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        source = None
        try:
            msg_type, payload = await asyncio.wait_for(read_message(reader), timeout=10)
            if msg_type != MSG_HELLO or unpack_hello(payload).get("proto") != PROTO_VERSION:
                log.warning("bad handshake from %s", peer)
                return
            source = self.source_factory()
            catalog = source.controls()
            writer.write(
                pack_hello(
                    game=self.game_name, width=source.width, height=source.height,
                    fps=self.fps, codec="jpeg", controls=catalog.to_json(),
                )  # fmt: skip
            )
            log.info("client connected: %s (%s, %d buttons)", peer, catalog.mode, len(catalog.buttons))
            await self._session(reader, writer, source)
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            log.info("client disconnected: %s", peer)
            if source is not None:
                source.close()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _session(self, reader, writer, source: FrameSource) -> None:
        catalog = source.controls()
        valid = {button.id for button in catalog.buttons}
        state = {"held": frozenset(), "seq": 0, "bye": False}

        async def read_loop() -> None:
            while True:
                msg_type, payload = await read_message(reader)
                if msg_type == MSG_INPUT:
                    seq, _client_ts, ids = unpack_input(payload)
                    if seq >= state["seq"]:
                        state["seq"] = seq
                        state["held"] = normalize_held(catalog.mode, valid, ids)
                elif msg_type == MSG_FEEDBACK:
                    fb = unpack_feedback(payload)
                    log.debug("feedback: %s", fb)
                elif msg_type == MSG_BYE:
                    state["bye"] = True
                    return

        reader_task = asyncio.create_task(read_loop())
        dt = 1.0 / self.fps
        next_t = time.monotonic()
        sent = dropped = 0
        last_report = time.monotonic()
        try:
            while not reader_task.done():
                source.step(state["held"])
                if writer.transport.get_write_buffer_size() > MAX_BACKLOG_BYTES:
                    dropped += 1
                else:
                    image = encode_frame(source.render(), self.quality)
                    header = FrameHeader(
                        tick=source.tick, base_tick=source.tick, input_seq=state["seq"],
                        server_ts=time.time(), deadline_ms=self.deadline_ms,
                    )  # fmt: skip
                    writer.write(pack_frame(header, image))
                    sent += 1
                next_t += dt
                now = time.monotonic()
                if next_t < now - 5 * dt:
                    next_t = now
                await asyncio.sleep(max(0.0, next_t - now))
                if now - last_report > 5:
                    log.info("sent=%d dropped=%d tick=%d", sent, dropped, source.tick)
                    last_report = now
        finally:
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader_task


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="0.0.0.0", help="interface to bind (default: all)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--source", default="stub", choices=["stub", "mame"], help="frame source")
    p.add_argument("--rom-dir", default="roms", help="directory with user-supplied ROMs (emulator sources)")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--quality", type=int, default=80, help="JPEG quality 1-95")
    p.add_argument("--deadline-ms", type=int, default=100, help="display deadline tag put on each frame")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(name)s %(message)s")

    try:
        make_source(args.source, rom_dir=args.rom_dir).close()
    except NotImplementedError as exc:
        raise SystemExit(str(exc))

    server = StreamServer(
        lambda: make_source(args.source, rom_dir=args.rom_dir),
        host=args.host, port=args.port, fps=args.fps, quality=args.quality,
        deadline_ms=args.deadline_ms, game_name=f"pacman/{args.source}",
    )  # fmt: skip

    async def run() -> None:
        await server.start()
        # Local launcher reads this line. It is not part of the wire protocol.
        print(f"CLOUDPAC_READY port={server.port}", flush=True)
        await server.serve_forever()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
