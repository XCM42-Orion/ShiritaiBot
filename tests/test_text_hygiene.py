from shiritaibot.agent import tool_outcome
from shiritaibot.core import canonical
from shiritaibot.core.text import clean_outbound, redact_secrets


def test_clean_outbound_strips_markdown():
    text = "## 标题\n**重点** 内容和 `代码`\n\n```python\nprint(1)\n```\n---\n[链接](https://example.com)"
    assert clean_outbound(text) == "标题\n重点 内容和 代码\n\nprint(1)\n\n链接 https://example.com"


def test_clean_outbound_flattens_table_rules():
    text = "| 名称 | 值 |\n| --- | --- |\n| a | 1 |"
    assert clean_outbound(text) == "名称 | 值\na | 1"


def test_clean_outbound_keeps_plain_text():
    text = "这个我会（）\n要不要试试？"
    assert clean_outbound(text) == text


def test_redact_known_secret_and_token_shapes():
    text = "key=abc123secretvalue token sk-abcdefghijklmnop1234"
    result = redact_secrets(text, ["abc123secretvalue"])
    assert "abc123secretvalue" not in result
    assert "sk-abcdefghijklmnop1234" not in result
    assert result.count("[已脱敏]") == 2


def test_redact_keeps_short_values():
    assert redact_secrets("abc", ["abc"]) == "abc"


def test_tool_outcome_reports_business_failure():
    ok, summary = tool_outcome(canonical({"ok": False, "status": "auth_expired", "message": "x"}))
    assert not ok
    assert summary


def test_tool_outcome_reports_business_success():
    ok, _ = tool_outcome(canonical({"ok": True, "status": "ok"}))
    assert ok
