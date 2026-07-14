from __future__ import annotations

from enum import Enum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from .policies import PolicyViolation, RunPolicy
from .schemas import AgentPhase


class HookDecision(str, Enum):
    ALLOW = "allow"
    REJECT = "reject"
    PAUSE = "pause"


class HookResult(BaseModel):
    decision: HookDecision = HookDecision.ALLOW
    reason: str = ""


class HookContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    phase: AgentPhase
    step_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentHook:
    name = "hook"
    order = 100

    async def handle(self, event: str, context: HookContext) -> HookResult:
        return HookResult()


class HookBus:
    def __init__(self, hooks: Iterable[AgentHook] = ()):
        self._hooks = sorted(hooks, key=lambda hook: hook.order)

    async def emit(self, event: str, context: HookContext) -> HookResult:
        for hook in self._hooks:
            result = await hook.handle(event, context)
            if result.decision != HookDecision.ALLOW:
                return result
        return HookResult()


class BudgetHook(AgentHook):
    name = "budget"
    order = 10

    def __init__(self, policy: RunPolicy):
        self.policy = policy

    async def handle(self, event: str, context: HookContext) -> HookResult:
        try:
            if event == "before_step":
                self.policy.ensure_step_allowed(
                    total_steps=int(context.payload.get("total_steps", 0)),
                    phase_steps=int(context.payload.get("phase_steps", 0)),
                )
            elif event == "before_tool":
                self.policy.ensure_tool_allowed(
                    str(context.payload.get("tool_name", "")),
                    set(context.payload.get("allowed_tools", [])),
                )
        except PolicyViolation as exc:
            return HookResult(decision=HookDecision.REJECT, reason=str(exc))
        return HookResult()


class ProgressHook(AgentHook):
    name = "progress"
    order = 80

    def __init__(self, repository):
        self.repository = repository

    async def handle(self, event: str, context: HookContext) -> HookResult:
        message = str(context.payload.get("message", ""))
        if message:
            self.repository.append_event(
                context.run_id,
                event,
                message,
                phase=context.phase,
                payload={k: v for k, v in context.payload.items() if k != "message"},
            )
        return HookResult()


class PersistenceHook(AgentHook):
    name = "persistence"
    order = 40


class TraceHook(AgentHook):
    name = "trace"
    order = 60


class RecoveryHook(AgentHook):
    name = "recovery"
    order = 90

