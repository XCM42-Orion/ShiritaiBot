import asyncio
import json
import sys

import pytest
from test_permissions import eventually

from shiritaibot.config import SessionPolicy, SubagentConfig
from shiritaibot.core import Context, Reply

OWNER = Context("web:owner", "owner", "agent", True)


async def test_instances_describe_only_enabled_config_without_cli_arguments(app):
    app.config.value.subagents = [
        SubagentConfig(
            id="coder",
            kind="codex",
            model="coding-model",
            permission="edit",
            command=["codex", "--private-argument"],
        ),
        SubagentConfig(id="reviewer", kind="claude_code", command=["claude"]),
        SubagentConfig(id="local", kind="generic", command=[sys.executable]),
        SubagentConfig(id="disabled", enabled=False),
    ]
    assert app.subagents.instances() == [
        {"id": "coder", "kind": "codex", "model": "coding-model", "permission": "edit"},
        {"id": "reviewer", "kind": "claude_code", "model": "", "permission": "read_only"},
        {"id": "local", "kind": "generic", "model": "", "permission": "read_only"},
    ]


async def test_instances_tool_tracks_reconfiguration_and_keeps_runs_separate(app):
    async def query():
        return json.loads(await app.registry.execute(OWNER, "subagent_instances", {}))

    await app.store.put("subagent_run", "old-run", {"instance": "removed"}, OWNER.session)
    assert await query() == []
    specs = {spec["function"]["name"]: spec["function"] for spec in app.registry.specs(OWNER)}
    assert "subagent_instances" in specs
    assert "subagent_instances" in specs["subagent_start"]["description"]
    assert specs["subagent_instances"]["parameters"]["additionalProperties"] is False

    settings = app.config.value.model_copy(deep=True)
    settings.subagents = [SubagentConfig(id="new", kind="generic", command=[sys.executable])]
    await app.reconfigure(settings)
    assert [instance["id"] for instance in await query()] == ["new"]
    # Queries read live config, including changes since tool registration.
    app.config.value.subagents[0].enabled = False
    assert await query() == []
    runs = json.loads(await app.registry.execute(OWNER, "subagent_list", {}))
    assert [run["id"] for run in runs] == ["old-run"]
    assert app.subagents.tasks == {}
    assert await app.store.list("approval") == []


@pytest.mark.parametrize(
    "ctx",
    [
        Context("web:owner", "owner", "chat", True),
        Context("group:qq:1", "member", "chat", group=True),
    ],
)
async def test_instances_tool_uses_existing_tool_permissions(app, ctx):
    app.config.value.subagents = [SubagentConfig(id="private")]
    app.config.value.policy.sessions[ctx.session] = SessionPolicy(mode="chat")
    assert "subagent_instances" not in [
        spec["function"]["name"] for spec in app.registry.specs(ctx)
    ]
    result = json.loads(await app.registry.execute(ctx, "subagent_instances", {}))
    assert "error" in result
    assert "private" not in json.dumps(result)


async def test_agent_discovers_instance_then_starts_it_with_existing_approval(app):
    app.config.value.subagents = [
        SubagentConfig(
            id="local",
            kind="generic",
            command=[sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
        )
    ]
    done = asyncio.Event()

    class LLM:
        async def complete(self, route, system, messages, tools, **kwargs):
            results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
            if not results:
                assert any(t["function"]["name"] == "subagent_instances" for t in tools)
                name, args = "subagent_instances", {}
            elif len(results) == 1:
                name = "subagent_start"
                args = {"instance": results[0][0]["id"], "prompt": "hello"}
            else:
                assert results[1]["instance"] == "local"
                done.set()
                return Reply(text="已委派")
            return Reply(
                calls=[
                    {
                        "id": f"call-{len(results)}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ]
            )

    app.runtime.llm = LLM()
    await app.runtime.submit(OWNER, "查询可用子代理并委派")
    approvals = await eventually(lambda: app.store.list("approval"))
    assert len(approvals) == 1
    assert approvals[0]["tool"] == "subagent_start"
    assert approvals[0]["args"] == {"instance": "local", "prompt": "hello"}
    assert await app.store.list("subagent_run") == []
    await app.permissions.resolve(approvals[0]["id"], True)
    await asyncio.wait_for(done.wait(), 3)

    async def completed():
        return [run for run in await app.store.list("subagent_run") if run["status"] == "completed"]

    runs = await eventually(completed)
    assert runs[0]["result"].strip() == "HELLO"
