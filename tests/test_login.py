import logging

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from uvicorn.config import LOGGING_CONFIG

from shiritaibot import cli, web
from shiritaibot.cli import LoginLinkFormatter


@pytest.mark.parametrize("colors", [True, False])
def test_startup_link_uses_bound_port_without_exposing_token_to_other_handlers(colors):
    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        "Uvicorn running on %s://%s:%d (Press CTRL+C to quit)",
        ("http", "127.0.0.1", 24680),
        None,
    )
    record.__dict__["color_message"] = record.msg
    formatter = LoginLinkFormatter(
        token="local-test-token", fmt="%(levelprefix)s %(message)s", use_colors=colors
    )

    terminal = formatter.format(record)
    assert "http://127.0.0.1:24680/token#token=local-test-token" in terminal
    assert terminal.count("local-test-token") == 1
    assert "local-test-token" not in record.getMessage()
    assert "local-test-token" not in record.__dict__["color_message"]
    assert "local-test-token" not in logging.Formatter("%(message)s").format(record)


def test_cli_uses_selected_home_token_and_preserves_uvicorn_defaults(tmp_path, monkeypatch):
    (tmp_path / "web.token").write_text("existing-local-token", encoding="utf-8")
    calls = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    result = CliRunner().invoke(cli.cli, ["run", "--home", str(tmp_path), "--port", "24680"])
    assert result.exit_code == 0, result.output
    app, options = calls[0]
    assert app.state.runtime.home == tmp_path
    assert options["host"] == "127.0.0.1"
    assert options["port"] == 24680
    assert options["access_log"] is False
    assert options["log_config"]["formatters"]["default"]["token"] == "existing-local-token"
    assert LOGGING_CONFIG["formatters"]["default"]["()"] == "uvicorn.logging.DefaultFormatter"
    assert "existing-local-token" not in result.output


def test_login_page_serves_app_without_granting_access_or_returning_token(tmp_path, monkeypatch):
    # Use a tiny build fixture so this security test also runs before npm build.
    fake_module = tmp_path / "src" / "shiritaibot" / "web" / "__init__.py"
    monkeypatch.setattr(web, "__file__", str(fake_module))
    dist = tmp_path / "web" / "dist"
    dist.mkdir(parents=True)
    page = "<!doctype html><html><body>Local console</body></html>"
    (dist / "index.html").write_text(page, encoding="utf-8")

    with TestClient(web.create_app(tmp_path / "data", token="local-test-token")) as client:
        response = client.get("/token")
        assert response.status_code == 200
        assert response.text == page
        assert response.headers["cache-control"] == "no-store"
        assert "local-test-token" not in response.text
        assert client.get("/api/health").status_code == 401
        assert (
            client.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
        )
        assert (
            client.get(
                "/api/health", headers={"Authorization": "Bearer local-test-token"}
            ).status_code
            == 200
        )


def test_token_retrieval_uses_explicit_home_from_another_directory(tmp_path, monkeypatch):
    home = tmp_path / "installed data"
    home.mkdir()
    path = home / "web.token"
    path.write_text("installed-token", encoding="utf-8")
    modified = path.stat().st_mtime_ns
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = CliRunner().invoke(cli.cli, ["token", "--home", str(home)])
    assert result.exit_code == 0
    assert result.output.strip() == "installed-token"
    assert path.stat().st_mtime_ns == modified
    assert not (elsewhere / "data").exists()


def test_token_retrieval_does_not_create_a_different_instance_token(tmp_path):
    home = tmp_path / "not-started"
    result = CliRunner().invoke(cli.cli, ["token", "--home", str(home)])
    assert result.exit_code == 2
    assert "无法读取管理令牌" in result.output
    assert "--home" in result.output
    assert not home.exists()
