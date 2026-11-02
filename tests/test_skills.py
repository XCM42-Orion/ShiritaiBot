import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shiritaibot.core import Context, PublicError
from shiritaibot.skill import parse_skill
from shiritaibot.tools.qq import session_dir
from shiritaibot.web import create_app

OWNER = Context("web:owner", "owner", mode="agent", owner=True)
EXAMPLE = Path(__file__).parents[1] / "examples" / "skills" / "skill-creator.skill"


def skill_md(name="demo", description="Demo skill. Use when testing."):
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nDo things.\n"


def skill_zip(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return data.getvalue()


async def call(app, name, args):
    app.config.value.policy.owner_default.preset = "full_access"
    return json.loads(await app.registry.execute(OWNER, name, args))


def test_frontmatter_validation():
    meta, body = parse_skill(skill_md())
    assert meta["name"] == "demo" and body.startswith("# demo")
    for bad in [
        "# no frontmatter",
        "---\nname: Bad_Name\ndescription: x\n---\n",
        "---\nname: ok\n---\n",
        "---\nname: ok\ndescription: x\n",
        "---\nname: [\n---\n",
    ]:
        with pytest.raises(PublicError):
            parse_skill(bad)


async def test_bundled_skill_creator_is_listed_and_injected(app):
    rows = {row["name"]: row for row in await app.skills.listing()}
    assert rows["skill-creator"]["source"] == "bundled" and not rows["skill-creator"]["error"]
    assert "skill-creator" in await app.skills.prompt()
    app.config.value.skills.disabled = ["skill-creator"]
    assert await app.skills.prompt() == ""
    result = await call(app, "skill_read", {"name": "skill-creator"})
    assert "已停用" in result["error"]


async def test_skill_tools_write_read_update_list_remove(app):
    created = await call(
        app,
        "skill_write",
        {
            "name": "demo",
            "skill_md": skill_md(),
            "files": [{"path": "references/notes.md", "content": "note body"}],
        },
    )
    assert created["written"] == "demo"
    assert (app.home / "skills" / "demo" / "references" / "notes.md").read_text() == "note body"
    again = await call(app, "skill_write", {"name": "demo", "skill_md": skill_md()})
    assert "已安装" in again["error"]

    listed = await call(app, "skill_list", {})
    assert {"demo", "skill-creator"} <= {row["name"] for row in listed["skills"]}

    read = await call(app, "skill_read", {"name": "demo", "path": "references/notes.md"})
    assert read["content"] == "note body"
    missing = await call(app, "skill_read", {"name": "demo", "path": "nope.md"})
    assert "references/notes.md" in missing["error"]
    escape = await call(app, "skill_read", {"name": "demo", "path": "../demo/SKILL.md"})
    assert "相对路径" in escape["error"]

    await call(app, "skill_update", {"name": "demo", "old_text": "Do things.", "new_text": "Do X."})
    assert "Do X." in (app.home / "skills" / "demo" / "SKILL.md").read_text()
    ambiguous = await call(
        app, "skill_update", {"name": "demo", "old_text": "demo", "new_text": "y"}
    )
    assert "出现了" in ambiguous["error"]
    rename = await call(app, "skill_update", {"name": "demo", "content": skill_md(name="other")})
    assert "改名" in rename["error"]
    await call(app, "skill_update", {"name": "demo", "path": "references/notes.md", "delete": True})
    assert not (app.home / "skills" / "demo" / "references" / "notes.md").exists()

    assert (await call(app, "skill_remove", {"name": "demo"}))["removed"] == "demo"
    assert not (app.home / "skills" / "demo").exists()
    bundled = await call(app, "skill_remove", {"name": "skill-creator"})
    assert "内置" in bundled["error"]


async def test_updating_bundled_skill_copies_it_and_removal_restores(app):
    await call(
        app,
        "skill_update",
        {"name": "skill-creator", "path": "references/extra.md", "content": "extra"},
    )
    rows = {row["name"]: row for row in await app.skills.listing()}
    assert rows["skill-creator"]["source"] == "installed" and rows["skill-creator"]["overrides"]
    assert (app.home / "skills" / "skill-creator" / "SKILL.md").is_file()
    assert not (app.skills.bundled / "skill-creator" / "references" / "extra.md").exists()
    result = await call(app, "skill_remove", {"name": "skill-creator"})
    assert result["bundled_restored"]


async def test_install_from_session_file_pack_and_bad_archives(app):
    workspace = session_dir(app.home, "workspaces", OWNER.session)
    workspace.mkdir(parents=True)
    # Downloaded QQ files carry hash names; the archive is detected by content.
    (workspace / "a1b2c3").write_bytes(
        skill_zip({"pkg/SKILL.md": skill_md("zipped"), "pkg/scripts/run.sh": "echo hi"})
    )
    installed = await call(app, "skill_install", {"path": "a1b2c3"})
    assert installed["installed"] == "zipped"
    assert sorted(installed["files"]) == ["SKILL.md", "scripts/run.sh"]
    duplicate = await call(app, "skill_install", {"path": "a1b2c3"})
    assert "replace" in duplicate["error"]
    assert (await call(app, "skill_install", {"path": "a1b2c3", "replace": True}))["installed"]

    (workspace / "SKILL.md").write_text(skill_md("plain"))
    assert (await call(app, "skill_install", {"path": "SKILL.md"}))["installed"] == "plain"

    packed = await call(app, "skill_pack", {"name": "zipped"})
    with zipfile.ZipFile(packed["path"]) as archive:
        assert sorted(archive.namelist()) == ["zipped/SKILL.md", "zipped/scripts/run.sh"]

    for data, message in [
        (skill_zip({"../evil/SKILL.md": skill_md()}), "不安全"),
        (skill_zip({"a/SKILL.md": skill_md("a"), "b/SKILL.md": skill_md("b")}), "只有一个"),
        (b"PK\x03\x04garbage", "ZIP"),
        (b"\xff\xfe\x00binary", "无法识别"),
    ]:
        with pytest.raises(PublicError, match=message):
            await app.skills.install_bytes(data)
    sensitive = await call(app, "skill_install", {"path": str(app.home / "config.toml")})
    assert "敏感" in sensitive["error"]


async def test_example_skill_package_installs(app):
    row = await app.skills.install_bytes(EXAMPLE.read_bytes())
    assert row["name"] == "skill-creator" and row["source"] == "installed"
    bundled = app.skills.bundled / "skill-creator" / "SKILL.md"
    assert row["content"] == bundled.read_text("utf-8")


async def test_skill_tools_follow_config_and_prompt(app):
    assert "skill_read" in app.registry.tools
    settings = app.config.value.model_copy(deep=True)
    settings.skills.enabled = False
    await app.reconfigure(settings)
    assert "skill_read" not in app.registry.tools
    assert await app.skills.prompt() == ""


def test_skill_web_api(tmp_path):
    with TestClient(create_app(tmp_path, token="t")) as client:
        headers = {"Authorization": "Bearer t"}
        rows = client.get("/api/skills", headers=headers).json()
        assert [row["name"] for row in rows] == ["skill-creator"]
        response = client.post(
            "/api/skills/install",
            headers=headers,
            content=skill_zip({"SKILL.md": skill_md("web-demo")}),
        )
        assert response.status_code == 200
        assert {row["name"] for row in response.json()} == {"skill-creator", "web-demo"}
        detail = client.get("/api/skills/web-demo", headers=headers).json()
        assert detail["files"] == [{"path": "SKILL.md", "bytes": len(skill_md("web-demo"))}]
        assert client.delete("/api/skills/web-demo", headers=headers).status_code == 200
        bad = client.post("/api/skills/install", headers=headers, content=b"not a skill")
        assert bad.status_code == 400
