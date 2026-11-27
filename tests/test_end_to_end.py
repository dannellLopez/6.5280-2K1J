import asyncio

from cloudpac.actions import Action
from cloudpac.client import StreamClient
from cloudpac.codec import decode_frame, encode_frame
from cloudpac.protocol import FrameHeader, pack_frame, unpack_frame
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


def test_trace_roundtrip(tmp_path):
    p = tmp_path / "t.jsonl"
    save_trace(p, [(0.5, Action.LEFT), (1.0, Action.UP)])
    assert load_trace(p) == [(0.5, Action.LEFT), (1.0, Action.UP)]


def test_server_client_loopback():
    async def scenario():
        server = StreamServer(lambda: make_source("stub"), host="127.0.0.1", port=0, fps=60)
        await server.start()
        client = StreamClient("127.0.0.1", server.port)
        params = await client.connect()
        assert params["width"] == StubPacman().width
        task = asyncio.create_task(client.run())
        await asyncio.sleep(0.3)
        client.send_input(Action.LEFT)
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
