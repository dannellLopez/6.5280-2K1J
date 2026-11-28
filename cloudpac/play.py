"""Run a local server process and a client process that talk over TCP.

    python -m cloudpac.play
    python -m cloudpac.play --headless --duration 5 --random-inputs

The server is a child process. The client is another child process. They use
the same port and the same messages as a remote session. Closing the client
stops the server.
"""

from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import threading
import time

_READY = "CLOUDPAC_READY port="


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def _wait_ready(proc: subprocess.Popen, *, quiet: bool, timeout: float) -> int:
    """Read the server's ready line without opening a TCP connection to it."""
    assert proc.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()
    tail: list[str] = []

    def reader() -> None:
        try:
            for line in proc.stdout:
                tail.append(line)
                del tail[:-30]
                if line.startswith(_READY):
                    lines.put(line)
                elif not quiet:
                    print(line, end="", flush=True)
        finally:
            lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            if proc.poll() is not None:
                raise ConnectionError(
                    f"server exited with status {proc.returncode} before it was ready:\n{''.join(tail)}"
                )
            continue
        if line is None:
            raise ConnectionError(f"server ended before it was ready (status {proc.poll()}):\n{''.join(tail)}")
        return int(line[len(_READY) :].strip())
    raise ConnectionError(f"timed out waiting for the local server:\n{''.join(tail)}")


def run_pair(
    client_args: list[str] | None = None,
    server_args: list[str] | None = None,
    *,
    quiet: bool = False,
    ready_timeout: float = 15.0,
) -> subprocess.CompletedProcess:
    """Start `python -m cloudpac.server`, then `python -m cloudpac.client` against it."""
    host = "127.0.0.1"
    server_cmd = [
        sys.executable, "-u", "-m", "cloudpac.server",
        "--host", host, "--port", "0",
        *(server_args or []),
    ]
    server = subprocess.Popen(
        server_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        port = _wait_ready(server, quiet=quiet, timeout=ready_timeout)
        if not quiet:
            print(f"local server on {host}:{port}", flush=True)
        client_cmd = [
            sys.executable, "-m", "cloudpac.client",
            "--host", host, "--port", str(port),
            *(client_args or []),
        ]
        return subprocess.run(client_cmd, capture_output=quiet, text=True)
    finally:
        _stop(server)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--headless", action="store_true", help="client prints stats instead of opening a window")
    p.add_argument("--duration", type=float, default=None, help="stop the client after this many seconds")
    p.add_argument("--scale", type=int, default=None, help="window zoom factor")
    p.add_argument("--random-inputs", action="store_true", help="press random buttons from the catalog")
    p.add_argument("--replay", help="play a trace file through the client")
    p.add_argument("--record", help="write the buttons the client sends")
    p.add_argument("--screenshot", help="headless: save the last frame to this PNG")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--source", choices=["stub", "mame"], default=None)
    p.add_argument("--fps", type=int, default=None)
    p.add_argument("--quality", type=int, default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    client_args: list[str] = []
    if args.headless:
        client_args.append("--headless")
    if args.duration is not None:
        client_args += ["--duration", str(args.duration)]
    if args.scale is not None:
        client_args += ["--scale", str(args.scale)]
    if args.random_inputs:
        client_args.append("--random-inputs")
    if args.replay:
        client_args += ["--replay", args.replay]
    if args.record:
        client_args += ["--record", args.record]
    if args.screenshot:
        client_args += ["--screenshot", args.screenshot]
    if args.seed is not None:
        client_args += ["--seed", str(args.seed)]
    if args.verbose:
        client_args.append("-v")

    server_args: list[str] = []
    if args.source:
        server_args += ["--source", args.source]
    if args.fps is not None:
        server_args += ["--fps", str(args.fps)]
    if args.quality is not None:
        server_args += ["--quality", str(args.quality)]
    if args.verbose:
        server_args.append("-v")

    try:
        result = run_pair(client_args, server_args)
    except ConnectionError as exc:
        raise SystemExit(str(exc))
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
