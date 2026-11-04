"""Plain-text hygiene for QQ outbound replies and tool output."""

from __future__ import annotations

import re

FENCE = re.compile(r"^[ \t]*```[^\n]*$", re.M)
HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M)
RULE = re.compile(r"^[ \t]{0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$", re.M)
TABLE_SEPARATOR = re.compile(r"^\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?$")
INLINE_CODE = re.compile(r"`([^`\n]+)`")
BOLD = re.compile(r"\*\*(?!\s)(.+?)\*\*", re.S)
UNDERSCORE = re.compile(r"(?<!\w)__([^_\n]+?)__(?!\w)", re.S)
STRIKE = re.compile(r"~~(.+?)~~", re.S)
LINK = re.compile(r"\[([^\]\n]*)\]\((https?://[^)\s]+)\)")
BLANKS = re.compile(r"\n{3,}")

REDACTED = "[已脱敏]"
TOKEN_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(
        r"(?i)((?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd)"
        r"\s*[:=]\s*)([\"']?)[A-Za-z0-9._~+/=-]{8,}\2"
    ),
)


def clean_outbound(text: str) -> str:
    """Strip Markdown syntax that QQ would show literally."""
    text = FENCE.sub("", text)
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if TABLE_SEPARATOR.match(stripped):
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            line = " | ".join(cell.strip() for cell in stripped[1:-1].split("|"))
        lines.append(line)
    text = "\n".join(lines)
    text = HEADING.sub("", text)
    text = RULE.sub("", text)
    text = INLINE_CODE.sub(r"\1", text)
    text = BOLD.sub(r"\1", text)
    text = UNDERSCORE.sub(r"\1", text)
    text = STRIKE.sub(r"\1", text)
    text = LINK.sub(lambda m: f"{m[1]} {m[2]}".strip(), text)
    text = BLANKS.sub("\n\n", text)
    return text.strip("\n")


def redact_secrets(text: str, secrets: list[str] | tuple[str, ...] = ()) -> str:
    """Hide known secret values and credential-looking tokens from tool output."""
    for secret in sorted((s for s in secrets if len(s) >= 6), key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    for pattern in TOKEN_PATTERNS:
        text = pattern.sub(lambda m: m[1] + REDACTED if m.groups() else REDACTED, text)
    return text
