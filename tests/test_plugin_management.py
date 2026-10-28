import io
import json
import stat
import sys
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from shiritaibot.config import PluginConfig
from shiritaibot.core import Context, PublicError
from shiritaibot.plugin.core import packages
from shiritaibot.web import create_app

SOURCE = """from shiritaibot.plugin_api import Plugin, schema
from .helper import VALUE
class Entry(Plugin):
    async def start(self):
        self.context.register_tool("managed_tool", "Example", schema({}), self.call, risk="safe")
    async def call(self, ctx, args):
        return VALUE
"""
MANIFEST = """api = 1
name = "Managed example"
[settings_schema]
type = "object"
[settings_schema.properties.interval]
type = "integer"
title = "检查间隔"
default = 60
"""


def plugin_zip(files=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for path, content in (
            files
            or {
                "repo-main/plugin.toml": MANIFEST,
                "repo-main/main.py": SOURCE,
                "repo-main/helper.py": 'VALUE = "FIRST"',
            }
        ).items():
            archive.writestr(path, content)
    return data.getvalue()


async def test_scan_registers_plugins_and_settings_with_old_config_and_bad_neighbors(app):
    app.config.value.plugins = [PluginConfig(id="missing", settings={"keep": 7})]
    directory = app.home / "plugins" / "new_plugin"
    directory.mkdir(parents=True)
    (directory / "plugin.toml").write_text(MANIFEST)
    (directory / "main.py").write_text("from shiritaibot.plugin_api import Plugin as Entry")
    broken = directory.with_name("broken")
    broken.mkdir()
    (broken / "plugin.toml").write_text("invalid [ toml")
    result = {p["id"]: p for p in await app.plugins.reload_all()}
    assert result["email"]["status"] == "disabled"
    assert result["new_plugin"]["settings_schema"]["properties"]["interval"]["title"] == "检查间隔"
    assert result["broken"]["status"] == "error"
    assert "missing" not in result
    configs = {p.id: p for p in app.config.value.plugins}
    assert configs["new_plugin"].settings == {"interval": 60}
    assert configs["missing"].settings == {"keep": 7}
    assert not configs["email"].enabled
    # Discovery is idempotent and does not overwrite user values.
    configs["new_plugin"].settings["interval"] = 120
    version = app.config.version
    await app.plugins.reload_all()
    assert app.config.version == version
    assert configs["new_plugin"].settings["interval"] == 120


async def test_deleted_plugin_disappears_and_reload_unloads_its_tools(app):
    await app.plugins.install("deleted", plugin_zip())
    cfg = next(p for p in app.config.value.plugins if p.id == "deleted")
    cfg.enabled = True
    await app.plugins.reload("deleted")
    closed = []
    original = app.plugins.loaded["deleted"].close

    async def close():
        closed.append(True)
        await original()

    app.plugins.loaded["deleted"].close = close
    # Losing the manifest makes this directory cease to be a registered plugin.
    (app.home / "plugins/deleted/plugin.toml").unlink()
    assert not any(p["id"] == "deleted" for p in await app.plugins.listing())
    await app.plugins.reload_all()
    assert closed == [True]
    assert "deleted" not in app.plugins.loaded
    assert "managed_tool" not in app.registry.tools
    assert "deleted" not in app.plugins.errors
    assert not any(name.startswith("shiritaibot_plugin_deleted_") for name in sys.modules)
    # Keep dormant settings so restoring the real plugin restores its configuration.
    assert next(p for p in app.config.value.plugins if p.id == "deleted").enabled


async def test_install_reload_relative_imports_and_delete_unregisters_everything(app):
    result = await app.plugins.install("managed", plugin_zip())
    assert any(p["id"] == "managed" for p in result)
    cfg = next(p for p in app.config.value.plugins if p.id == "managed")
    cfg.enabled = True
    await app.plugins.reload("managed")
    ctx = Context("web:owner", "owner", "agent", True)
    assert json.loads(await app.registry.execute(ctx, "managed_tool", {})) == "FIRST"
    old_tool = app.registry.tools["managed_tool"]
    (app.home / "plugins/managed/helper.py").write_text('VALUE = "OTHER"')
    await app.plugins.reload("managed")
    assert json.loads(await app.registry.execute(ctx, "managed_tool", {})) == "OTHER"
    with pytest.raises(PublicError):
        await old_tool.handler(ctx, {})
    # A failure leaves the active instance intact and is visible in the list.
    (app.home / "plugins/managed/main.py").write_text("syntax error :::")
    result = {p["id"]: p for p in await app.plugins.reload_all()}
    assert result["managed"]["status"] == "error"
    assert json.loads(await app.registry.execute(ctx, "managed_tool", {})) == "OTHER"
    data = app.home / "plugin_data/managed/keep.txt"
    data.write_text("retain history")
    result = await app.plugins.remove("managed")
    assert not any(p["id"] == "managed" for p in result)
    assert not (app.home / "plugins/managed").exists()
    assert "managed_tool" not in app.registry.tools
    assert "managed" not in app.plugins.loaded
    assert not any(p.id == "managed" for p in app.config.value.plugins)
    assert data.read_text() == "retain history"
    assert not any(name.startswith("shiritaibot_plugin_managed_") for name in sys.modules)
    assert not any(p["id"] == "managed" for p in await app.plugins.reload_all())


def test_startup_with_old_config_discovers_email_and_protected_install_lifecycle(tmp_path):
    (tmp_path / "config.toml").write_text("plugins = []\n")
    with TestClient(create_app(tmp_path, token="admin")) as client:
        for method, path in [
            ("GET", "/api/plugins"),
            ("POST", "/api/plugins/reload"),
            ("POST", "/api/plugins/install/zip/managed"),
            ("POST", "/api/plugins/install/github"),
            ("DELETE", "/api/plugins/email"),
        ]:
            assert client.request(method, path).status_code == 401
        client.headers["Authorization"] = "Bearer admin"
        configs = client.get("/api/config").json()["plugins"]
        assert "email" in {p["id"] for p in configs}
        response = client.post("/api/plugins/install/zip/managed", content=plugin_zip())
        assert response.status_code == 200, response.text
        assert any(p["id"] == "managed" for p in response.json())
        assert (
            client.post("/api/plugins/install/zip/managed", content=plugin_zip()).status_code == 400
        )
        config = client.get("/api/config").json()
        next(p for p in config["plugins"] if p["id"] == "managed")["enabled"] = True
        assert client.put("/api/config", json=config).status_code == 200
        assert "managed_tool" in client.get("/api/health").json()["tools"]
        assert client.delete("/api/plugins/managed").status_code == 200
        assert "managed_tool" not in client.get("/api/health").json()["tools"]
        assert not (tmp_path / "plugins/managed").exists()
        assert client.post("/api/plugins/reload").status_code == 200


def test_configured_ids_without_directories_never_appear_in_plugin_api(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[[plugins]]\nid = "orphan_enabled"\nenabled = true\n'
        '[[plugins]]\nid = "orphan_disabled"\nenabled = false\n'
    )
    with TestClient(create_app(tmp_path, token="admin")) as client:
        client.headers["Authorization"] = "Bearer admin"
        for response in [client.get("/api/plugins"), client.post("/api/plugins/reload")]:
            assert response.status_code == 200
            ids = {p["id"] for p in response.json()}
            assert "email" in ids
            assert not ids.intersection({"orphan_enabled", "orphan_disabled"})
            assert all(p["status"] != "missing" for p in response.json())
        assert not any(
            p["kind"] == "plugin.failed" for p in client.get("/api/records/activity").json()
        )


@pytest.mark.parametrize(
    "files",
    [
        {"../escape": "bad", "plugin.toml": MANIFEST, "main.py": SOURCE},
        {"/absolute": "bad", "plugin.toml": MANIFEST, "main.py": SOURCE},
        {"folder\\escape": "bad", "plugin.toml": MANIFEST, "main.py": SOURCE},
        {"plugin.toml": "api = 999", "main.py": SOURCE},
        {"plugin.toml": MANIFEST, "main.py": "syntax error :"},
        {"plugin.toml": 'api = 1\nsettings_schema = "bad"', "main.py": SOURCE},
        {"plugin.toml": MANIFEST},
    ],
)
async def test_reject_invalid_packages_without_installing_or_registering(app, files):
    with pytest.raises(PublicError):
        await app.plugins.install("invalid", plugin_zip(files))
    assert not (app.home / "plugins/invalid").exists()
    assert not any(p.id == "invalid" for p in app.config.value.plugins)


async def test_reject_zip_symlinks_and_reserved_ids(app):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        link = zipfile.ZipInfo("link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../outside")
    with pytest.raises(PublicError):
        await app.plugins.install("invalid", data.getvalue())
    for key in ["core", "../bad", "__pycache__"]:
        with pytest.raises(PublicError):
            await app.plugins.install(key, plugin_zip())


async def test_github_install_downloads_archive_without_executing_code(app, monkeypatch):
    original_client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=plugin_zip())

    monkeypatch.setattr(
        packages.httpx,
        "AsyncClient",
        lambda **kw: original_client(
            **kw,
            transport=httpx.MockTransport(handle),
        ),
    )
    archive = await packages.download_github("https://github.com/owner/plugin.git")
    await app.plugins.install("from_github", archive)
    assert str(requests[0].url) == "https://api.github.com/repos/owner/plugin/zipball"
    assert "from_github" not in app.plugins.loaded
    assert (app.home / "plugins/from_github/plugin.toml").is_file()
    for url in ["file:///tmp/x", "https://evil.test/o/r", "https://github.com/o/r/tree/main"]:
        with pytest.raises(PublicError):
            await packages.download_github(url)
    assert len(requests) == 1


async def test_schema_secrets_are_encrypted_and_new_defaults_reach_runtime(app):
    manifest = (
        MANIFEST
        + """
[settings_schema.properties.service_key]
type = "string"
writeOnly = true
default = ""
"""
    )
    await app.plugins.install(
        "schema_plugin",
        plugin_zip(
            {
                "plugin.toml": manifest,
                "main.py": "from shiritaibot.plugin_api import Plugin as Entry",
            }
        ),
    )
    config = app.config.value.model_copy(deep=True)
    cfg = next(p for p in config.plugins if p.id == "schema_plugin")
    cfg.enabled = True
    cfg.settings = {"service_key": "private-schema-key"}
    await app.reconfigure(config)
    saved = next(p for p in app.config.value.plugins if p.id == cfg.id)
    assert saved.settings["service_key"].startswith("secret:")
    assert await app.vault.resolve(saved.settings["service_key"]) == "private-schema-key"
    assert "private-schema-key" not in app.config.path.read_text()
    plugin = app.plugins.loaded[cfg.id]
    assert plugin.context.settings["interval"] == 60
    saved.settings["interval"] = "invalid-value"
    with pytest.raises(PublicError, match="interval") as raised:
        await app.plugins.reload(cfg.id)
    assert "invalid-value" not in str(raised.value)
    assert app.plugins.loaded[cfg.id] is plugin
