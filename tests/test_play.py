from cloudpac.play import run_pair


def test_server_and_client_are_separate_processes():
    result = run_pair(["--headless", "--duration", "2", "--random-inputs"], quiet=True)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, output
    assert "frames" in output
    assert "'frames': 0" not in output
    assert "'inputs_measured': 0" not in output
