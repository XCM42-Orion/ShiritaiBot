import asyncio
import json
import sys
from pathlib import Path

import pytest
from test_permissions import eventually

from shiritaibot.config import SessionPolicy, SubagentConfig
from shiritaibot.core import Context, PublicError, Reply
from shiritaibot.proactive import delegated
from shiritaibot.subagent import command_for, parse_output, question_in

OWNER = Context("web:owner", "owner", "agent", True)


async def test_instances_describe_only_enabled_config_without_cli_arguments(app):
    app.config.value.subagents = [
        SubagentConfig(
            id="coder",
            kind="codex",
            model="coding-model",
            network_access=True,
            command=["codex", "--private-argument"],
        ),
        SubagentConfig(id="reviewer", kind="claude_code", command=["claude"]),
        SubagentConfig(id="local", kind="generic", command=[sys.executable]),
        SubagentConfig(id="disabled", enabled=False),
    ]
    assert app.subagents.instances() == [
        {
            "id": "coder",
            "kind": "codex",
            "model": "coding-model",
            "permissions": {"sandbox": "workspace-write", "network_access": True, "add_dirs": []},
        },
        {
            "id": "reviewer",
            "kind": "claude_code",
            "model": "",
            "permissions": {
                "permission_mode": "acceptEdits",
                "tools": "default",
                "allowed_tools": ["Bash", "WebFetch", "WebSearch"],
                "disallowed_tools": [],
                "add_dirs": [],
            },
        },
        {"id": "local", "kind": "generic", "model": "", "permissions": {}},
    ]
    assert "--private-argument" not in json.dumps(app.subagents.instances())


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
                assert "结束本轮" in results[1]["note"]
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


def test_legacy_permission_migrates_to_cli_specific_settings():
    read = SubagentConfig.model_validate({"id": "a", "kind": "codex", "permission": "read_only"})
    assert read.sandbox == "read-only"
    assert (read.permission_mode, read.tools, read.allowed_tools) == (
        "dontAsk",
        ["Read", "Glob", "Grep"],
        [],
    )
    edit = SubagentConfig.model_validate({"id": "a", "kind": "claude_code", "permission": "edit"})
    assert edit == SubagentConfig(id="a", kind="claude_code")
    with pytest.raises(ValueError):
        SubagentConfig(id="a", add_dirs=["relative/path"])
    with pytest.raises(ValueError):
        SubagentConfig(id="a", allowed_tools=["rm -rf"])


def test_codex_permissions_map_to_config_overrides_for_fresh_and_resumed_runs():
    config = SubagentConfig(
        id="c", kind="codex", network_access=True, add_dirs=["/srv/repo"], model="m"
    )
    fresh = command_for(config, Path("/w"))
    assert fresh[:2] == ["codex", "exec"] and fresh[-3:] == ["-C", "/w", "-"]
    resumed = command_for(config, Path("/w"), "thread-1")
    assert resumed[:3] == ["codex", "exec", "resume"] and resumed[-2:] == ["thread-1", "-"]
    for command in (fresh, resumed):
        assert "sandbox_workspace_write.network_access=true" in command
        assert 'sandbox_workspace_write.writable_roots=["/srv/repo"]' in command
    full = command_for(SubagentConfig(id="c", sandbox="danger-full-access"), Path("/w"))
    assert not any(arg.startswith("sandbox_workspace_write") for arg in full)


def test_claude_permissions_map_to_cli_flags():
    config = SubagentConfig(
        id="c",
        kind="claude_code",
        command=["claude"],
        permission_mode="dontAsk",
        tools=["Read", "Bash"],
        allowed_tools=["Read", "Bash(git *)"],
        disallowed_tools=["Bash(rm *)"],
        add_dirs=["/srv/repo"],
    )
    command = command_for(config, Path("/w"), "session-1")

    def flag(name):
        return command[command.index(name) + 1]

    assert flag("--permission-mode") == "dontAsk"
    assert flag("--tools") == "Read,Bash"
    assert flag("--allowedTools") == "Read,Bash(git *)"
    assert flag("--disallowedTools") == "Bash(rm *)"
    assert flag("--add-dir") == "/srv/repo"
    assert flag("--resume") == "session-1"


def test_needs_input_marker_and_output_parsing():
    assert question_in("做完了一半\n\n**NEEDS_INPUT: 用哪个分支？**") == "用哪个分支？"
    assert question_in("全部完成") is None
    parsed = parse_output(
        "claude_code",
        json.dumps(
            {
                "result": "无法写入",
                "session_id": "s",
                "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "ls"}}],
            }
        ),
    )
    assert parsed["native"] == "s" and parsed["denied"] == ["Bash(ls)"]


FAKE_CLAUDE = """
import json, sys
prompt = sys.stdin.read()
assert "NEEDS_INPUT" in prompt
if "--resume" in sys.argv:
    text = "用了 " + prompt.split("\\n")[0]
else:
    text = "已检查仓库\\nNEEDS_INPUT: 要部署到哪个环境？"
print(json.dumps({"result": text, "session_id": "native-1", "is_error": False}))
"""


async def test_subagent_question_activates_proactive_loop_and_resumes(app, tmp_path):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE)
    app.config.value.subagents = [
        SubagentConfig(id="cc", kind="claude_code", command=[sys.executable, str(script)])
    ]
    events = []

    async def emit(event):
        events.append(event)
        return event.dedup_key

    app.subagents.emit = emit
    row = await app.subagents.start(OWNER, "cc", "部署服务")

    async def status(expected):
        current = await app.store.get("subagent_run", row["id"])
        return current if current["status"] == expected else None

    waiting = await eventually(lambda: status("needs_input"))
    assert waiting["question"] == "要部署到哪个环境？"
    assert waiting["native_session_id"] == "native-1"
    assert events[0].kind == "subagent.needs_input" and events[0].urgency == "high"
    assert events[0].payload["question"] == "要部署到哪个环境？"
    assert delegated(events[0].model_dump(mode="json"))

    # The Proactive loop (another owner session) answers in the same native session.
    proactive = Context("background:proactive:x", "owner", "agent", True, background=True)
    assert "subagent_send" in [s["function"]["name"] for s in app.registry.specs(proactive)]
    sent = await app.subagents.send(proactive, row["id"], "staging")
    assert sent["status"] == "queued"
    with pytest.raises(PublicError):
        await app.subagents.send(proactive, row["id"], "again while running")
    done = await eventually(lambda: status("completed"))
    assert done["result"] == "用了 staging" and done["round"] == 1
    assert done["scope"] == OWNER.session and "question" not in done
    assert [e.dedup_key for e in events] == [f"{row['id']}:0", f"{row['id']}:1"]
    assert events[1].kind == "subagent.completed" and events[1].payload["reply"] == "staging"

    stranger = Context("private:qq:9", "9", "agent")
    with pytest.raises(PublicError):
        await app.subagents.send(stranger, row["id"], "hi")


def test_deepseek_harness_permissions_map_to_headless_flags():
    config = SubagentConfig(
        id="d",
        kind="deepseek_harness",
        command=["dsh"],
        permission_preset="danger-full-access",
        add_dirs=["/srv/repo/.git"],
        model="ignored",
    )
    fresh = command_for(config, Path("/w"))
    assert fresh[:4] == ["dsh", "--profile", "headless", "--json"] and fresh[-1] == "-"

    def flag(command, name):
        return command[command.index(name) + 1]

    assert flag(fresh, "--permission-mode") == "danger-full-access"
    assert flag(fresh, "--approval-policy") == "never"
    assert flag(fresh, "--add-dir") == "/srv/repo/.git"
    assert "--session-id" not in fresh and "ignored" not in fresh
    resumed = command_for(config, Path("/w"), "session-1")
    assert flag(resumed, "--session-id") == "session-1"
    # Policy options are per invocation, so a resume repeats them.
    assert flag(resumed, "--permission-mode") == "danger-full-access"
    with pytest.raises(ValueError):
        SubagentConfig(id="d", kind="deepseek_harness", permission_preset="custom")


def test_deepseek_harness_output_parsing():
    lines = [
        {"type": "session", "sessionId": "session-1", "cwd": "/w"},
        {"type": "tool_call", "callId": "c1", "tool": "bash", "input": {"command": "rm -rf x"}},
        {
            "type": "tool_result",
            "callId": "c1",
            "status": "error",
            "result": "",
            "error": {"name": "ApprovalError", "code": "APPROVAL_REJECTED"},
        },
        {"type": "text", "text": "partial"},
        {
            "type": "final",
            "sessionId": "session-1",
            "reason": {"kind": "completed"},
            "text": "done",
        },
    ]
    parsed = parse_output("deepseek_harness", "\n".join(json.dumps(x) for x in lines))
    assert parsed == {
        "result": "done",
        "native": "session-1",
        "error": False,
        "denied": ["bash(rm -rf x)"],
    }
    aborted = parse_output(
        "deepseek_harness",
        json.dumps({"type": "final", "sessionId": "s", "reason": {"kind": "aborted"}, "text": ""}),
    )
    assert aborted["error"] and aborted["native"] == "s"
    failed = parse_output("deepseek_harness", json.dumps({"type": "error", "message": "no key"}))
    assert failed["error"] and failed["result"] == "no key"


FAKE_DSH = """
import json, sys
prompt = sys.stdin.read()
assert sys.argv[1:4] == ["--profile", "headless", "--json"] and sys.argv[-1] == "-"
resume = sys.argv[sys.argv.index("--session-id") + 1] if "--session-id" in sys.argv else None
sid = resume or "session-abc"
print(json.dumps({"type": "session", "sessionId": sid, "cwd": "."}))
text = "用了 " + prompt.split("\\n")[0] if resume else "已检查\\nNEEDS_INPUT: 哪个分支？"
print(json.dumps({"type": "final", "sessionId": sid, "reason": {"kind": "completed"}, "text": text}))
"""


async def test_deepseek_harness_run_asks_and_resumes_in_same_session(app, tmp_path):
    script = tmp_path / "fake_dsh.py"
    script.write_text(FAKE_DSH)
    app.config.value.subagents = [
        SubagentConfig(id="ds", kind="deepseek_harness", command=[sys.executable, str(script)])
    ]

    async def emit(event):
        return event.dedup_key

    app.subagents.emit = emit
    row = await app.subagents.start(OWNER, "ds", "修 bug")

    async def status(expected):
        current = await app.store.get("subagent_run", row["id"])
        return current if current["status"] == expected else None

    waiting = await eventually(lambda: status("needs_input"))
    assert waiting["question"] == "哪个分支？" and waiting["native_session_id"] == "session-abc"
    await app.subagents.send(OWNER, row["id"], "main")
    done = await eventually(lambda: status("completed"))
    assert done["result"] == "用了 main" and done["exit_code"] == 0
