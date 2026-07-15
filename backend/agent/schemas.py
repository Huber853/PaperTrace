from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentPhase(str, Enum):
    PLAN = "plan"
    DISCOVER = "discover"
    EXTRACT = "extract"
    ANALYZE = "analyze"
    SYNTHESIZE = "synthesize"
    VERIFY = "verify"
    FINALIZE = "finalize"


class ToolAction(BaseModel):
    action: Literal["tool"] = "tool"
    tool_name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any]
    rationale: str = Field(min_length=1, max_length=500)


class CompletePhase(BaseModel):
    action: Literal["complete_phase"] = "complete_phase"
    summary: str = Field(min_length=1, max_length=1000)
    artifact_ids: list[str] = Field(min_length=1)


class RequestInput(BaseModel):
    action: Literal["request_input"] = "request_input"
    question: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)


class AbortRun(BaseModel):
    action: Literal["abort_run"] = "abort_run"
    error_code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)


AgentAction = Annotated[
    ToolAction | CompletePhase | RequestInput | AbortRun,
    Field(discriminator="action"),
]


class ToolResult(BaseModel):
    status: Literal["success", "partial", "failed"]
    data: Any = None
    summary: str = ""
    artifact_kind: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    metrics: dict[str, int | float] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

