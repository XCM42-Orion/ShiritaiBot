from unittest.mock import Mock

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from shiritaibot import cli
from shiritaibot.web import create_app


def test_restart_requires_auth_and_coalesces_repeated_requests(tmp_path):
    restart = Mock()
    with TestClient(create_app(tmp_path, token="admin", request_restart=restart)) as client:
        assert client.post("/api/restart").status_code == 401
        restart.assert_not_called()
        client.headers["Authorization"] = "Bearer admin"
        health = client.get("/api/health").json()
        assert health["restart_available"] is True
        for _ in range(2):
            response = client.post("/api/restart")
            assert response.status_code == 202
            assert response.json() == {"status": "restarting", "instance_id": health["instance_id"]}
        restart.assert_called_once_with()


def test_unmanaged_server_does_not_claim_to_restart(tmp_path):
    with TestClient(create_app(tmp_path, token="admin")) as client:
        client.headers["Authorization"] = "Bearer admin"
        assert client.get("/api/health").json()["restart_available"] is False
        assert client.post("/api/restart").status_code == 503


def test_cli_reexecutes_original_command_only_after_shutdown(tmp_path, monkeypatch):
    events = []

    class Server:
        should_exit = False

        def __init__(self, config):
            self.config = config

        def run(self):
            with TestClient(self.config.app) as client:
                token = (tmp_path / "web.token").read_text()
                client.headers["Authorization"] = f"Bearer {token}"
                assert client.post("/api/restart").status_code == 202
                assert self.should_exit
            events.append("closed")

    def reexec(executable, argv):
        assert events == ["closed"]
        assert executable == cli.sys.executable
        assert argv == [executable, "-m", "shiritaibot.cli", "run", "--home", str(tmp_path)]
        events.append("exec")

    monkeypatch.setattr(cli.uvicorn, "Server", Server)
    monkeypatch.setattr(cli, "_port_in_use", lambda port: False)
    monkeypatch.setattr(cli.os, "execv", reexec)
    monkeypatch.setattr(
        cli.sys, "orig_argv", ["python", "-m", "shiritaibot.cli", "run", "--home", str(tmp_path)]
    )
    result = CliRunner().invoke(cli.cli, ["run", "--home", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert events == ["closed", "exec"]


def test_cli_reports_port_conflict_without_starting(tmp_path, monkeypatch):
    def fail_server(config):
        raise AssertionError("server must not start when the port is busy")

    monkeypatch.setattr(cli.uvicorn, "Server", fail_server)
    monkeypatch.setattr(cli, "_port_in_use", lambda port: True)
    result = CliRunner().invoke(cli.cli, ["run", "--home", str(tmp_path)])
    assert result.exit_code == 1
    assert "已被占用" in result.output
