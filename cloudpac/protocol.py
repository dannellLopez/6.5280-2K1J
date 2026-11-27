"""Wire protocol: length-prefixed binary messages over one TCP connection.

    message := type:u8  length:u32  payload[length]          (network byte order)

Message types
    HELLO     json. client -> server first ({"proto", "client"}), server replies
              with stream parameters ({"proto", "game", "width", "height", "fps", "codec"}).
    INPUT     client -> server: seq:u32 action:u8 client_ts:f64
    FRAME     server -> client: FrameHeader + encoded image bytes
    FEEDBACK  client -> server: periodic receive statistics (input for future
              adaptive pacing / bitrate control)
    BYE       either direction, empty payload.

FRAME header fields map onto the spec's "frames tagged with the starting game
state, input sequence, and display deadline":
    tick         game tick this frame depicts
    base_tick    tick of the game state it was rendered from (== tick for
                 non-speculative frames; earlier for speculative ones)
    input_seq    highest client input seq applied to the state shown; the
                 client uses this to measure input-to-display latency
    deadline_ms  how long after server_ts the frame is still useful
    flags        bit 0: speculative
    branch_id    which speculative alternative (0 = the actual state)
"""

from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import dataclass

PROTO_VERSION = 1

MSG_HELLO = 1
MSG_INPUT = 2
MSG_FRAME = 3
MSG_FEEDBACK = 4
MSG_BYE = 5

FLAG_SPECULATIVE = 0x01

_MSG = struct.Struct("!BI")
_INPUT = struct.Struct("!IBd")
_FRAME = struct.Struct("!IIIdIBB")
_FEEDBACK = struct.Struct("!IIQI")

MAX_PAYLOAD = 16 * 1024 * 1024


@dataclass
class FrameHeader:
    tick: int
    base_tick: int
    input_seq: int
    server_ts: float
    deadline_ms: int
    flags: int = 0
    branch_id: int = 0

    @property
    def speculative(self) -> bool:
        return bool(self.flags & FLAG_SPECULATIVE)


@dataclass
class Feedback:
    last_tick: int
    frames_received: int
    bytes_received: int
    interval_ms: int


def pack_message(msg_type: int, payload: bytes = b"") -> bytes:
    return _MSG.pack(msg_type, len(payload)) + payload


async def read_message(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    head = await reader.readexactly(_MSG.size)
    msg_type, length = _MSG.unpack(head)
    if length > MAX_PAYLOAD:
        raise ValueError(f"payload too large: {length}")
    payload = await reader.readexactly(length) if length else b""
    return msg_type, payload


def pack_hello(**fields) -> bytes:
    return pack_message(MSG_HELLO, json.dumps({"proto": PROTO_VERSION, **fields}).encode())


def unpack_hello(payload: bytes) -> dict:
    return json.loads(payload.decode())


def pack_input(seq: int, action: int, client_ts: float) -> bytes:
    return pack_message(MSG_INPUT, _INPUT.pack(seq, int(action), client_ts))


def unpack_input(payload: bytes) -> tuple[int, int, float]:
    return _INPUT.unpack(payload)


def pack_frame(header: FrameHeader, image: bytes) -> bytes:
    h = _FRAME.pack(
        header.tick, header.base_tick, header.input_seq, header.server_ts,
        header.deadline_ms, header.flags, header.branch_id,
    )  # fmt: skip
    return pack_message(MSG_FRAME, h + image)


def unpack_frame(payload: bytes) -> tuple[FrameHeader, bytes]:
    fields = _FRAME.unpack_from(payload)
    return FrameHeader(*fields), payload[_FRAME.size :]


def pack_feedback(fb: Feedback) -> bytes:
    return pack_message(MSG_FEEDBACK, _FEEDBACK.pack(fb.last_tick, fb.frames_received, fb.bytes_received, fb.interval_ms))


def unpack_feedback(payload: bytes) -> Feedback:
    return Feedback(*_FEEDBACK.unpack(payload))
