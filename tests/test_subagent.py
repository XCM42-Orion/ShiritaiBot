import asyncio
import json
import sys
from pathlib import Path

import pytest
from test_permissions import eventually

import shiritaibot.subagent
from shiritaibot.config import SessionPolicy, SubagentConfig
from shiritaibot.core import Context, PublicError, Reply
from shiritaibot.proactive import delegated
from shiritaibot.subagent import OUTPUT_LIMIT, clip, command_for, parse_output, question_in

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


# Issue #23: runs whose stdout passes the limit must not lose the events at its tail.
NOISE = "x" * 1000
CODEX_NOISE = {
    "type": "item.completed",
    "item": {"type": "command_execution", "aggregated_output": NOISE},
}
CODEX_START = {"type": "thread.started", "thread_id": "thread-1"}
DSH_START = {"type": "session", "sessionId": "session-1"}
DSH_NOISE = {"type": "tool_result", "callId": "n", "status": "ok", "result": NOISE}


def codex_message(text):
    return {"type": "item.completed", "item": {"type": "agent_message", "text": text}}


def dsh_final(text):
    return {
        "type": "final",
        "sessionId": "session-1",
        "reason": {"kind": "completed"},
        "text": text,
    }


async def fake_run(app, tmp_path, kind, lines="", stderr=""):
    """Run a fake CLI that prints lines, returning its final row and emitted event."""
    data = tmp_path / "stdout.txt"
    data.write_text(lines, encoding="utf-8")
    script = tmp_path / "fake_cli.py"
    script.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.stdout.buffer.write(open({str(data)!r}, 'rb').read())\n"
    )
    app.config.value.subagents = [
        SubagentConfig(id="cli", kind=kind, command=[sys.executable, str(script)])
    ]
    events = []

    async def emit(event):
        events.append(event)
        return event.dedup_key

    app.subagents.emit = emit
    row = await app.subagents.start(OWNER, "cli", "任务")
    await eventually(lambda: asyncio.sleep(0, bool(events)))
    return await app.store.get("subagent_run", row["id"]), events[0]


def stream(start, noise, *tail):
    size = OUTPUT_LIMIT // len(json.dumps(noise)) + 10
    return "\n".join(json.dumps(line) for line in [start, *[noise] * size, *tail]) + "\n"


@pytest.mark.parametrize(
    ("kind", "start", "noise", "last", "native"),
    [
        ("codex", CODEX_START, CODEX_NOISE, codex_message, "thread-1"),
        ("deepseek_harness", DSH_START, DSH_NOISE, dsh_final, "session-1"),
    ],
)
async def test_long_stream_keeps_tail_events(app, tmp_path, kind, start, noise, last, native):
    lines = stream(start, noise, last("修好了"))
    assert len(lines) > OUTPUT_LIMIT
    row, event = await fake_run(app, tmp_path, kind, lines)
    assert row["status"] == "completed" and row["exit_code"] == 0
    assert row["truncated"] and row["native_session_id"] == native
    assert row["result"].startswith("修好了") and "[输出截断]" in row["result"]
    assert "[输出截断]" in event.payload["result"]

    row, event = await fake_run(
        app, tmp_path, kind, stream(start, noise, last("做了一半\nNEEDS_INPUT: 哪个分支？"))
    )
    assert row["status"] == "needs_input" and row["question"] == "哪个分支？"
    assert row["native_session_id"] == native and "[输出截断]" in row["result"]
    assert event.kind == "subagent.needs_input"


async def test_codex_oversized_event_does_not_hide_final_message(app, tmp_path):
    huge = {"item": {"type": "command_execution", "aggregated_output": "x" * (5 * 1024 * 1024)}}
    lines = "\n".join(json.dumps(x) for x in [CODEX_START, huge, codex_message("修好了")])
    row, _ = await fake_run(app, tmp_path, "codex", lines)
    assert row["status"] == "completed" and row["result"].startswith("修好了")
    assert row["truncated"] and row["native_session_id"] == "thread-1"


@pytest.mark.parametrize(
    ("kind", "start", "noise", "tail", "text"),
    [
        (
            "codex",
            CODEX_START,
            CODEX_NOISE,
            {"type": "turn.failed", "error": {"message": "额度用尽"}},
            "额度用尽",
        ),
        (
            "deepseek_harness",
            DSH_START,
            DSH_NOISE,
            {"type": "error", "message": "no key"},
            "no key",
        ),
    ],
)
async def test_long_stream_detects_failure_at_tail(app, tmp_path, kind, start, noise, tail, text):
    row, _ = await fake_run(app, tmp_path, kind, stream(start, noise, tail))
    assert row["status"] == "failed" and row["exit_code"] == 1
    assert row["result"].startswith(text) and "[输出截断]" in row["result"]


@pytest.mark.parametrize(
    ("kind", "start", "noise"),
    [
        ("codex", CODEX_START, CODEX_NOISE),
        ("deepseek_harness", DSH_START, DSH_NOISE),
    ],
)
async def test_long_stream_without_final_result_fails(app, tmp_path, kind, start, noise):
    row, _ = await fake_run(app, tmp_path, kind, stream(start, noise))
    assert row["status"] == "failed" and row["exit_code"] == 1
    assert row["truncated"] and "未能取得最终结果" in row["result"]
    assert NOISE[:100] not in row["result"]


async def test_deepseek_denial_past_the_limit_is_reported(app, tmp_path):
    lines = stream(
        DSH_START,
        DSH_NOISE,
        {"type": "tool_call", "callId": "c1", "tool": "bash", "input": {"command": "rm x"}},
        {"type": "tool_result", "callId": "c1", "error": {"code": "APPROVAL_REJECTED"}},
        dsh_final("没做完"),
    )
    row, _ = await fake_run(app, tmp_path, "deepseek_harness", lines)
    assert row["status"] == "needs_input" and "bash(rm x)" in row["question"]
    assert "[输出截断]" in row["result"]


def claude_output(text):
    return json.dumps(
        {
            "is_error": False,
            "result": text,
            "session_id": "native-1",
            "permission_denials": [
                {"tool_name": "Write", "tool_input": {"file_path": "a", "content": NOISE * 300}}
            ],
        }
    )


async def test_claude_large_output_stays_parseable(app, tmp_path):
    lines = claude_output("x" * 1000 + "\nNEEDS_INPUT: 哪个环境？")
    assert len(lines) > OUTPUT_LIMIT
    row, _ = await fake_run(app, tmp_path, "claude_code", lines)
    assert row["status"] == "needs_input" and row["question"] == "哪个环境？"
    assert row["native_session_id"] == "native-1" and not row["truncated"]
    assert "Write(a)" in row["result"]


async def test_claude_truncated_json_fails_instead_of_completing(app, tmp_path, monkeypatch):
    monkeypatch.setitem(shiritaibot.subagent.OUTPUT_LIMITS, "claude_code", 1000)
    row, event = await fake_run(app, tmp_path, "claude_code", claude_output("完成"))
    assert row["status"] == "failed" and row["exit_code"] == 1 and row["truncated"]
    assert "未能取得最终结果" in row["result"] and "[输出截断]" in row["result"]
    assert "is_error" not in row["result"]
    assert event.payload["status"] == "failed"


async def test_truncation_is_visible_even_when_parsing_succeeds(app, tmp_path, monkeypatch):
    monkeypatch.setitem(shiritaibot.subagent.OUTPUT_LIMITS, "claude_code", 1000)
    lines = json.dumps({"result": "完成", "session_id": "native-1"})
    row, _ = await fake_run(app, tmp_path, "claude_code", lines, stderr="e" * 2000)
    assert row["status"] == "completed" and row["truncated"]
    assert row["native_session_id"] == "native-1"
    assert row["result"].startswith("完成") and "[输出截断]" in row["result"]


async def test_generic_truncation_notice_survives_result_limit(app, tmp_path):
    row, _ = await fake_run(app, tmp_path, "generic", "x" * (OUTPUT_LIMIT + 1))
    assert row["status"] == "completed" and row["truncated"]
    assert len(row["result"]) <= 16000 and row["result"].endswith("可能不完整。")


def test_clip_keeps_both_ends_within_limit():
    assert clip("short", 100) == "short"
    clipped = clip("头" + "x" * 1000 + "尾", 100)
    assert len(clipped) == 100 and clipped.startswith("头") and clipped.endswith("尾")


def test_parsing_ignores_malformed_events_and_splits_only_on_newlines():
    lines = ['{"item": "text"}', "[1]", json.dumps(codex_message("甲\u2028乙"), ensure_ascii=False)]
    assert parse_output("codex", "\n".join(lines))["result"] == "甲\u2028乙"
