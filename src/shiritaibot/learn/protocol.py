"""Minimal, school-independent OneLearn stdio request/response contract.

This is an implementation subset, not a claim of conformance to a final OneLearn
standard. A process receives one UTF-8 JSON object and returns one JSON object.
"""

from typing import Any, Literal

from pydantic import Field

from shiritaibot.core import Model


class LearnRequest(Model):
    version: Literal[1] = 1
    operation: Literal[
        "login",
        "verify_sms",
        "courses",
        "notices",
        "homework",
        "files",
        "homework_attachments",
        "download",
        "download_homework_attachment",
    ]
    params: dict[str, Any] = Field(default_factory=dict)
    owner: str = "owner"
    config: dict[str, Any] = Field(default_factory=dict)
    data_dir: str


class LearnResponse(Model):
    ok: bool
    data: Any = None
    status: str = ""
    message: str = ""
