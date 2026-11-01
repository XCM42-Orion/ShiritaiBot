import json
import subprocess
import sys

import httpx
import pytest
from test_permissions import eventually

from shiritaibot.config import PluginConfig, SubagentConfig
from shiritaibot.core import Context

OWNER = Context("web:owner", "owner", "agent", True)
TOKEN = "ghp_test_token_value"
IDENTITY = {"commit_name": "shiritai-bot", "commit_email": "bot@example.com"}


async def call(app, name, args, ctx=OWNER):
    return json.loads(await app.registry.execute(ctx, name, args))


async def test_plugin_process_env_reaches_shell_and_subagents_and_is_revoked(app):
    app.config.value.policy.owner_default.preset = "full_access"
    directory = app.home / "plugins" / "envplug"
    directory.mkdir(parents=True)
    (directory / "plugin.toml").write_text('api = 1\nname = "env"', encoding="utf-8")
    (directory / "main.py").write_text(
        "from shiritaibot.plugin_api import Plugin\n"
        "class Entry(Plugin):\n"
        "    async def start(self):\n"
        "        self.context.set_process_env({'SHIRITAI_TEST_ID': 'from-plugin'})\n",
        encoding="utf-8",
    )
    app.config.value.plugins.append(PluginConfig(id="envplug", enabled=True))
    await app.plugins.reload("envplug")
    result = await call(app, "shell", {"command": 'printf "%s" "$SHIRITAI_TEST_ID"'})
    assert result["stdout"] == "from-plugin"

    app.config.value.subagents = [
        SubagentConfig(
            id="env",
            kind="generic",
            command=[sys.executable, "-c", "import os; print(os.environ['SHIRITAI_TEST_ID'])"],
        )
    ]
    row = await app.subagents.start(OWNER, "env", "")
    key = row["id"] if "id" in row else next(iter(app.subagents.tasks))

    async def done():
        run = await app.store.get("subagent_run", key)
        return run if run["status"] in {"completed", "failed"} else None

    run = await eventually(done)
    assert run["status"] == "completed" and run["result"].strip() == "from-plugin"

    app.config.value.plugins[-1].enabled = False
    await app.plugins.reload("envplug")
    assert app.plugins.child_env() is None
    result = await call(app, "shell", {"command": 'printf "%s" "${SHIRITAI_TEST_ID:-none}"'})
    assert result["stdout"] == "none"


def test_set_process_env_rejects_invalid_names(tmp_path):
    from shiritaibot.plugin_api import PluginContext

    async def nothing(*_):
        return ""

    context = PluginContext("x", {}, tmp_path, nothing, nothing)
    with pytest.raises(ValueError):
        context.set_process_env({"lower": "x"})


class FakeGitHub:
    def __init__(self):
        self.requests: list[tuple[str, str, dict | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "shiritai-bot", "id": 42})
        if path == "/repos/shiritai-bot/demo":
            return httpx.Response(
                200,
                json={
                    "full_name": "shiritai-bot/demo",
                    "private": False,
                    "default_branch": "main",
                    "html_url": "https://github.com/shiritai-bot/demo",
                    "owner": {"login": "shiritai-bot"},
                },
            )
        if path == "/repos/shiritai-bot/demo/issues" and request.method == "POST":
            return httpx.Response(201, json={"number": 7, "title": body["title"], "state": "open"})
        if path == "/repos/shiritai-bot/missing":
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(500, json={"message": f"unexpected {request.method} {path}"})


async def enable_github(app, monkeypatch, **settings):
    monkeypatch.setenv("SHIRITAI_GH_TEST_TOKEN", TOKEN)
    app.config.value.policy.owner_default.preset = "full_access"
    app.config.value.plugins = [
        PluginConfig(
            id="github",
            enabled=True,
            settings={"token": "env:SHIRITAI_GH_TEST_TOKEN", **IDENTITY, **settings},
        )
    ]
    await app.plugins.reload("github")
    plugin = app.plugins.loaded["github"]
    fake = FakeGitHub()
    await plugin.client.aclose()
    plugin.client = httpx.AsyncClient(
        base_url="https://api.github.com",
        headers=dict(plugin.client.headers),
        transport=httpx.MockTransport(fake),
    )
    return plugin, fake


async def test_github_tools_are_risk_tiered_and_identity_is_injected(app, monkeypatch):
    plugin, _ = await enable_github(app, monkeypatch)
    listing = next(p for p in await app.plugins.listing() if p["id"] == "github")
    assert {t["name"]: t["risk"] for t in listing["tools"]} == {
        "github_read": "safe",
        "github_write": "normal",
        "github_publish": "sensitive",
        "github_admin": "dangerous",
        "github_push": "sensitive",
    }
    admin = app.registry.tools["github_admin"].schema["properties"]["action"]["enum"]
    assert not any("delete" in action for action in admin)
    env = app.plugins.child_env()
    assert env["GIT_AUTHOR_NAME"] == env["GIT_COMMITTER_NAME"] == "shiritai-bot"
    assert env["GIT_AUTHOR_EMAIL"] == "bot@example.com"
    assert TOKEN not in json.dumps(app.plugins.process_env)
    assert set(plugin.context.process_env) == {
        "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"
    }  # fmt: skip


async def test_github_derives_noreply_identity_when_blank(app, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"login": "shiritai-bot", "id": 42})

    real = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    await enable_github(app, monkeypatch, commit_name="", commit_email="")
    env = app.plugins.child_env()
    assert env["GIT_AUTHOR_NAME"] == "shiritai-bot"
    assert env["GIT_AUTHOR_EMAIL"] == "42+shiritai-bot@users.noreply.github.com"


async def test_github_enforces_owner_context_and_allowed_owners(app, monkeypatch):
    _, fake = await enable_github(app, monkeypatch)
    repo = await call(app, "github_read", {"action": "repo", "repo": "shiritai-bot/demo"})
    assert repo["default_branch"] == "main"
    # Empty allowed_owners means only the token account itself.
    other = await call(app, "github_read", {"action": "repo", "repo": "someone/else"})
    assert "不在插件允许操作的账号范围内" in other["error"]
    missing = await call(app, "github_read", {"action": "repo", "repo": "shiritai-bot/missing"})
    assert "404" in missing["error"]
    group = Context("group:qq:1", "owner", "agent", True, group=True)
    denied = await call(app, "github_read", {"action": "whoami"}, group)
    assert "error" in denied and not any(p == "/user" for _, p, _ in fake.requests[3:])
    issue = await call(
        app,
        "github_write",
        {"action": "issue_create", "repo": "shiritai-bot/demo", "title": "Hi", "body": "x"},
    )
    assert issue["number"] == 7
    assert fake.requests[-1][2] == {"title": "Hi", "body": "x"}
    refused = await call(
        app,
        "github_publish",
        {"action": "branch_delete", "repo": "shiritai-bot/demo", "branch": "main"},
    )
    assert "默认分支" in refused["error"]


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


async def test_github_push_uses_plugin_mirror_without_running_workspace_hooks(
    app, monkeypatch, tmp_path
):
    plugin, _ = await enable_github(app, monkeypatch)
    remote_root = tmp_path / "remote"
    remote = remote_root / "shiritai-bot" / "demo.git"
    remote.mkdir(parents=True)
    git(remote, "init", "--bare", "--quiet")
    plugin.git_url = remote_root.as_uri() + "/"

    workspace = plugin.context.session_workspace(OWNER.session) / "demo"
    workspace.mkdir(parents=True)
    git(workspace, "init", "--quiet", "-b", "feature")
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(
        workspace,
        "-c",
        "user.name=x",
        "-c",
        "user.email=x@example.com",
        "commit",
        "--quiet",
        "-m",
        "init",
    )
    marker = tmp_path / "hook-ran"
    hook = workspace / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\nenv > {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    git(workspace, "config", "core.hooksPath", str(workspace / ".git" / "hooks"))

    result = await call(
        app, "github_push", {"repo": "shiritai-bot/demo", "branch": "feature", "path": "demo"}
    )
    assert result.get("branch") == "feature", result
    assert git(remote, "rev-parse", "refs/heads/feature")[:12] == result["sha"]
    assert not marker.exists()

    escape = await call(
        app, "github_push", {"repo": "shiritai-bot/demo", "branch": "feature", "path": "../.."}
    )
    assert "工作目录内" in escape["error"]
    forced = await call(
        app,
        "github_push",
        {"repo": "shiritai-bot/demo", "branch": "feature", "target": "main", "path": "demo",
         "force": True},
    )  # fmt: skip
    assert "默认分支" in forced["error"]
