from __future__ import annotations

from pydantic import BaseModel, Field


class PolicyViolation(RuntimeError):
    """A bounded-loop policy rejected an otherwise valid action."""


class RunPolicy(BaseModel):
    max_steps: int = Field(20, ge=1, le=200)
    max_steps_per_phase: int = Field(4, ge=1, le=50)
    max_tool_repeats: int = Field(2, ge=1, le=10)
    tool_retry_count: int = Field(1, ge=0, le=5)
    verification_revisions: int = Field(1, ge=0, le=5)
    phase_timeout_seconds: int = Field(300, ge=1, le=3600)

    def ensure_step_allowed(self, *, total_steps: int, phase_steps: int) -> None:
        if total_steps >= self.max_steps:
            raise PolicyViolation("run step budget exceeded")
        if phase_steps >= self.max_steps_per_phase:
            raise PolicyViolation("phase step budget exceeded")

    @staticmethod
    def ensure_tool_allowed(tool_name: str, allowed_tools: set[str]) -> None:
        if tool_name not in allowed_tools:
            raise PolicyViolation(f"tool {tool_name!r} is not allowed in this phase")

    def ensure_tool_repeat(self, arguments_hash: str, previous_hashes: list[str]) -> None:
        repeats = sum(item == arguments_hash for item in previous_hashes)
        if repeats >= self.max_tool_repeats:
            raise PolicyViolation("tool call repeated too many times")

