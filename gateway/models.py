"""Pydantic models shared by the registry, the executor and the API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    """One tool an agent may call, as declared in the catalog."""

    name: str = Field(..., pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    description: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    url: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    allowed_callers: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = Field(default=60, ge=1)
    timeout_s: float = Field(default=2.0, gt=0)
    retries: int = Field(default=1, ge=0, le=5)


class ToolCallRequest(BaseModel):
    """A request from an agent to run one tool."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    caller: str = Field(..., min_length=1, max_length=128)
    dry_run: bool = False


class ToolCallResult(BaseModel):
    """The outcome of one tool call, as written to the audit table."""

    tool: str
    status: Literal["ok", "error", "denied", "invalid", "timeout"]
    latency_ms: float
    output: Any = None
    error: str | None = None
    audit_id: int | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "ok"
