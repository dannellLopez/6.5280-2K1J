import asyncio
import time

from cloudpac.client import StreamClient
from cloudpac.codec import decode_frame, encode_frame
from cloudpac.controls import HOLD_MANY, SELECT_ONE, normalize_held
from cloudpac.protocol import FLAG_SPECULATIVE, FrameHeader, pack_frame, pack_input, unpack_frame, unpack_input
from cloudpac.replay import load_trace, save_trace
from cloudpac.server import StreamServer
from cloudpac.sources import make_source
from cloudpac.sources.stub_pacman import StubPacman


def test_frame_header_roundtrip():
    h = FrameHeader(tick=5, base_tick=4, input_seq=3, server_ts=1.5, deadline_ms=100, flags=1, branch_id=2)
    msg = pack_frame(h, b"abc")
    got, img = unpack_frame(msg[5:])
    assert got == h and img == b"abc" and got.speculative


def test_codec_roundtrip():
    frame = StubPacman().render()
    out = decode_frame(encode_frame(frame, 90))
    assert out.shape == frame.shape


def test_input_roundtrip_and_trailing_bytes():
    payload = pack_input(4, [2, 0, 7], 1.25)[5:]
    seq, ts, ids = unpack_input(payload)
    assert (seq, ids) == (4, (2, 0, 7))
    assert abs(ts - 1.25) < 1e-9
    assert unpack_input(payload + b"\xff\x00")[2] == (2, 0, 7)
    assert unpack_input(pack_input(1, [], 0.0)[5:]) == (1, 0.0, ())


def test_normalize_held():
    valid = {0, 1, 2, 3}
    assert normalize_held(SELECT_ONE, valid, [3, 1, 9]) == frozenset({1})
    assert normalize_held(HOLD_MANY, valid, [3, 1, 9]) == frozenset({1, 3})


def test_speculative_frame_is_not_displayed():
    client = StreamClient("127.0.0.1")
    client._pending[1] = time.monotonic()
    guessed = FrameHeader(tick=1, base_tick=0, input_seq=1, server_ts=0.0, deadline_ms=100, flags=FLAG_SPECULATIVE)
    client._on_frame(guessed, b"not-a-jpeg", 11)
    assert client.latest is None and client.stats.frames == 1 and client.stats.latencies_ms == []
    image = encode_frame(StubPacman().render(), 40)
    real = FrameHeader(tick=2, base_tick=2, input_seq=1, server_ts=0.0, deadline_ms=100)
    client._on_frame(real, image, len(image))
    assert client.latest is not None and client.latest[0].tick == 2
    assert len(client.stats.latencies_ms) == 1


def test_trace_roundtrip(tmp_path):
    p = tmp_path / "t.jsonl"
    save_trace(p, [(0.5, (2,)), (1.0, (0,))])
    assert load_trace(p) == [(0.5, (2,)), (1.0, (0,))]
    legacy = tmp_path / "old.jsonl"
    legacy.write_text('{"t": 0.25, "action": "LEFT"}\n')
    assert load_trace(legacy) == [(0.25, (2,))]


def test_server_client_loopback():
    async def scenario():
        server = StreamServer(lambda: make_source("stub"), host="127.0.0.1", port=0, fps=60)
        await server.start()
        client = StreamClient("127.0.0.1", server.port)
        params = await client.connect()
        assert params["width"] == StubPacman().width
        assert params["controls"]["mode"] == "select_one"
        assert [b["id"] for b in params["controls"]["buttons"]] == [0, 1, 2, 3]
        task = asyncio.create_task(client.run())
        await asyncio.sleep(0.3)
        client.send_buttons([2])
        await asyncio.sleep(0.5)
        summary = client.stats.summary()
        header, rgb = client.latest
        await client.close()
        task.cancel()
        await server.stop()
        return summary, header, rgb

    summary, header, rgb = asyncio.run(scenario())
    assert summary["frames"] > 15
    assert summary["inputs_measured"] == 1
    assert summary["input_to_frame_ms"]["max"] < 500
    assert header.input_seq == 1 and not header.speculative
    assert rgb.shape[2] == 3
