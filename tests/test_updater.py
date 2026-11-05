import asyncio
import io
import json
import tarfile
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from shiritaibot.core import PublicError
from shiritaibot.updater import MARKER, Updater
from shiritaibot.web import create_app

SHA = "a" * 40
HEAD = "b" * 40


def release(tag, prerelease=False, draft=False):
    return {
        "tag_name": tag,
        "name": f"ShiritaiBot {tag}",
        "body": f"## {tag}\n- change",
        "prerelease": prerelease,
        "draft": draft,
        "published_at": "2026-10-01T00:00:00Z",
        "html_url": f"https://github.com/x/releases/{tag}",
    }


def commit(sha, message):
    return {
        "sha": sha,
        "html_url": f"https://github.com/x/commit/{sha}",
        "commit": {
            "message": message + "\n\nbody",
            "author": {"name": "dev"},
            "committer": {"date": "2026-11-01T00:00:00Z"},
        },
    }


def archive(files: dict[str, str], top="ShiritaiBot-1.1.0") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(
        fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": SHA}
    ) as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


SOURCE = {
    "src/shiritaibot/cli.py": "new",
    "src/shiritaibot/plugin/core/__init__.py": "core",
    "src/shiritaibot/plugin/github/main.py": "new github",
    "pyproject.toml": "new",
    "uv.lock": "new",
    "web/package-lock.json": "{}",
}


def github(releases, calls=None, tarball=None, ahead=2):
    def handler(request: httpx.Request):
        if calls is not None:
            calls.append(str(request.url))
        path = request.url.path
        if request.url.host == "codeload.github.com":
            return httpx.Response(200, content=tarball or archive(SOURCE))
        if path.endswith("/releases"):
            page = int(request.url.params["page"])
            return httpx.Response(200, json=releases[(page - 1) * 100 : page * 100])
        if path.endswith("/commits/main"):
            return httpx.Response(200, json=commit(HEAD, "feat: unreleased"))
        if "/compare/" in path:
            return httpx.Response(
                200,
                json={
                    "ahead_by": ahead,
                    "commits": [commit("c" * 40, "fix: one"), commit(HEAD, "feat: unreleased")],
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def installed(tmp_path):
    root = tmp_path / "install"
    app = root / "app"
    (app / "src/shiritaibot/plugin/core").mkdir(parents=True)
    (app / "src/shiritaibot/plugin/github").mkdir()
    (app / "src/shiritaibot/plugin/feishu").mkdir()
    (app / "src/shiritaibot/plugin/feishu/main.py").write_text("user plugin")
    (app / "src/shiritaibot/plugin/github/main.py").write_text("old github")
    (app / "src/shiritaibot/cli.py").write_text("old")
    (app / "obsolete.txt").write_text("old")
    (app / ".venv").mkdir()
    (app / ".venv/marker").write_text("venv")
    (root / ".shiritaibot-installer").write_text("shiritaibot-installer-v1\n")
    return app


async def test_releases_paginate_filter_and_cache(tmp_path):
    rows = [release(f"v1.{i}.0", prerelease=i % 3 == 0) for i in range(150, 0, -1)]
    rows.insert(1, release("v9.9.9", draft=True))
    calls = []
    updater = Updater(tmp_path, restart=Mock(), transport=github(rows, calls))
    stable = await updater.releases(1, 10, prerelease=False, refresh=False)
    assert stable["total"] == 100
    assert all(not r["prerelease"] for r in stable["items"])
    assert stable["items"][0]["tag"] == "v1.149.0"
    everything = await updater.releases(2, 10, prerelease=True, refresh=False)
    assert everything["total"] == 150
    assert everything["items"][0]["tag"] == "v1.140.0"
    assert len([c for c in calls if "/releases" in c]) == 2  # two GitHub pages, then cached
    await updater.releases(1, 10, prerelease=False, refresh=True)
    assert len([c for c in calls if "/releases" in c]) == 4


async def test_preview_lists_commits_since_latest_release(tmp_path):
    updater = Updater(tmp_path, restart=Mock(), transport=github([release("v1.0.0")]))
    preview = await updater.preview()
    assert preview["sha"] == HEAD
    assert preview["base"] == "v1.0.0"
    assert preview["message"] == "feat: unreleased"
    assert [c["message"] for c in preview["commits"]] == ["feat: unreleased", "fix: one"]
    up_to_date = Updater(tmp_path, restart=Mock(), transport=github([release("v1.0.0")], ahead=0))
    assert await up_to_date.preview() is None


async def test_rate_limit_is_reported_in_plain_language(tmp_path):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    )
    with pytest.raises(PublicError, match="访问次数已用完"):
        await Updater(tmp_path, restart=Mock(), transport=transport).releases(1, 5, False, False)


async def test_update_requires_installer_layout_and_known_tag(tmp_path):
    app = installed(tmp_path)
    updater = Updater(app, restart=None)
    with pytest.raises(PublicError, match="shiritaibot run"):
        await updater.apply("release", "v1.0.0")
    (app.parent / ".shiritaibot-installer").unlink()
    updater = Updater(app, restart=Mock())
    with pytest.raises(PublicError, match="一键部署"):
        await updater.apply("release", "v1.0.0")
    (app.parent / ".shiritaibot-installer").write_text("x")
    updater = Updater(app, restart=Mock(), transport=github([release("v1.0.0")]))
    with pytest.raises(PublicError, match="找不到该版本"):
        await updater.apply("release", "v2.0.0")
    with pytest.raises(PublicError, match="提交编号"):
        await updater.apply("commit", "main")


def fake_commands(updater, fail_sync=False):
    ran = []

    async def command(argv, cwd):
        ran.append((argv, cwd))
        if "build" in argv:
            (cwd / "web/dist").mkdir(parents=True)
            (cwd / "web/dist/index.html").write_text("ui")
        if "sync" in argv and fail_sync and len([a for a, _ in ran if "sync" in a]) == 1:
            raise PublicError("uv 执行失败")

    updater.command = command
    updater.npm = lambda *args: ["npm", *args]
    return ran


async def test_update_swaps_source_keeps_venv_and_plugins_then_restarts(tmp_path, monkeypatch):
    monkeypatch.setenv("UV", "uv")
    app = installed(tmp_path)
    restart = Mock()
    updater = Updater(app, restart=restart, transport=github([release("v1.1.0")]))
    ran = fake_commands(updater)
    state = await updater.apply("release", "v1.1.0")
    assert state["phase"] == "downloading"
    await updater.task
    assert updater.state["phase"] == "restarting", updater.status()
    restart.assert_called_once_with()
    assert (app / "src/shiritaibot/cli.py").read_text() == "new"
    assert (app / "src/shiritaibot/plugin/github/main.py").read_text() == "new github"
    assert (app / "src/shiritaibot/plugin/feishu/main.py").read_text() == "user plugin"
    assert (app / ".venv/marker").read_text() == "venv"
    assert (app / "web/dist/index.html").read_text() == "ui"
    assert not (app / "obsolete.txt").exists()
    assert (app.parent / ".update/previous/obsolete.txt").exists()
    assert not list((app.parent / ".update").glob("stage-*"))
    marker = json.loads((app / MARKER).read_text())
    assert marker["kind"] == "release" and marker["ref"] == "v1.1.0" and marker["sha"] == SHA
    assert updater.current()["ref"] == "v1.1.0"
    sync = [argv for argv, cwd in ran if "sync" in argv]
    assert sync == [["uv", "sync", "--frozen", "--no-dev", "--inexact", "--no-install-project"]]
    assert [cwd for argv, cwd in ran if "sync" in argv] == [app]


async def test_failed_dependency_sync_restores_previous_source(tmp_path, monkeypatch):
    monkeypatch.setenv("UV", "uv")
    app = installed(tmp_path)
    restart = Mock()
    updater = Updater(app, restart=restart, transport=github([]))
    ran = fake_commands(updater, fail_sync=True)
    await updater.apply("commit", HEAD)
    await updater.task
    assert updater.state["phase"] == "failed"
    assert "uv" in updater.state["message"]
    restart.assert_not_called()
    assert (app / "src/shiritaibot/cli.py").read_text() == "old"
    assert (app / "obsolete.txt").exists()
    assert (app / "src/shiritaibot/plugin/feishu/main.py").read_text() == "user plugin"
    assert not (app / MARKER).exists()
    assert len([a for a, _ in ran if "sync" in a]) == 2  # failed sync, then re-sync old lock


async def test_inspect_reports_local_changes_and_writes_patch(tmp_path):
    app = installed(tmp_path)
    updater = Updater(app, restart=Mock(), transport=github([release("v1.1.0")]))
    result = await updater.inspect("release", "v1.1.0")
    files = {row["path"]: row["kind"] for row in result["files"]}
    assert files["src/shiritaibot/cli.py"] == "modified"
    assert files["src/shiritaibot/plugin/github/main.py"] == "modified"
    assert files["src/shiritaibot/plugin/feishu/main.py"] == "removed"
    assert files["obsolete.txt"] == "removed"
    assert ".venv/marker" not in files
    patch = updater.root / result["patch"]
    assert patch.is_file() and patch.parent == updater.root / "data/patches"
    assert patch.name.startswith("local-") and patch.suffix == ".patch"
    text = patch.read_text(encoding="utf-8")
    assert "+++ local/src/shiritaibot/cli.py" in text
    assert "+old" in text
    assert "+++ local/obsolete.txt" in text


async def test_apply_reuses_the_inspected_download(tmp_path, monkeypatch):
    monkeypatch.setenv("UV", "uv")
    app = installed(tmp_path)
    calls = []
    updater = Updater(app, restart=Mock(), transport=github([release("v1.1.0")], calls))
    fake_commands(updater)
    await updater.inspect("release", "v1.1.0")
    await updater.apply("release", "v1.1.0")
    await updater.task
    assert updater.state["phase"] == "restarting", updater.status()
    assert len([c for c in calls if "codeload.github.com" in c]) == 1


async def test_incomplete_archive_is_rejected_before_touching_app(tmp_path):
    app = installed(tmp_path)
    tarball = archive({"README.md": "nothing"})
    updater = Updater(app, restart=Mock(), transport=github([], tarball=tarball))
    fake_commands(updater)
    await updater.apply("commit", HEAD)
    await updater.task
    assert updater.state["phase"] == "failed"
    assert "不完整" in updater.state["message"]
    assert (app / "src/shiritaibot/cli.py").read_text() == "old"


def test_update_endpoints(tmp_path):
    app = installed(tmp_path)
    updater = Updater(app, restart=Mock(), transport=github([release("v1.0.0")]))
    with TestClient(create_app(tmp_path / "home", token="admin", updater=updater)) as client:
        assert client.get("/api/update/status").status_code == 401
        client.headers["Authorization"] = "Bearer admin"
        status = client.get("/api/update/status").json()
        assert status["available"] is True and status["phase"] == "idle"
        listing = client.get("/api/update/releases", params={"size": 5}).json()
        assert listing["items"][0]["tag"] == "v1.0.0"
        assert client.get("/api/update/preview").json()["commit"]["sha"] == HEAD
        bad = client.post("/api/update/apply", json={"kind": "branch", "ref": "main"})
        assert bad.status_code == 422
        inspected = client.post("/api/update/inspect", json={"kind": "release", "ref": "v1.0.0"})
        assert inspected.status_code == 200
        assert inspected.json()["files"][0]["path"]


def test_default_updater_is_unavailable_outside_installer(tmp_path):
    with TestClient(create_app(tmp_path, token="admin", request_restart=Mock())) as client:
        client.headers["Authorization"] = "Bearer admin"
        status = client.get("/api/update/status").json()
        assert status["available"] is False
        assert "git" in status["reason"]
        assert (
            client.post("/api/update/apply", json={"kind": "commit", "ref": SHA}).status_code == 400
        )


async def test_concurrent_update_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("UV", "uv")
    app = installed(tmp_path)
    updater = Updater(app, restart=Mock(), transport=github([]))
    gate = asyncio.Event()
    fake_commands(updater)
    original = updater.command

    async def slow(argv, cwd):
        await gate.wait()
        await original(argv, cwd)

    updater.command = slow
    await updater.apply("commit", HEAD)
    with pytest.raises(PublicError, match="已有更新"):
        await updater.apply("commit", HEAD)
    with pytest.raises(PublicError, match="已有更新"):
        await updater.inspect("commit", HEAD)
    gate.set()
    await updater.task
