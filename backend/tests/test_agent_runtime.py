from __future__ import annotations

import asyncio

import pytest
from pydantic import TypeAdapter, ValidationError

from agent.hooks import AgentHook, HookBus, HookContext, HookDecision, HookResult
from agent.policies import PolicyViolation, RunPolicy
from agent.schemas import AgentAction, AgentPhase, CompletePhase, ToolAction


def test_agent_action_is_discriminated_and_validated():
    adapter = TypeAdapter(AgentAction)
    action = adapter.validate_python(
        {
            "action": "tool",
            "tool_name": "search_papers",
            "arguments": {"query": "remote work"},
            "rationale": "需要先收集论文",
        }
    )
    assert isinstance(action, ToolAction)

    complete = adapter.validate_python(
        {
            "action": "complete_phase",
            "summary": "规划完成",
            "artifact_ids": ["artifact-1"],
        }
    )
    assert isinstance(complete, CompletePhase)

    with pytest.raises(ValidationError):
        adapter.validate_python({"action": "tool", "tool_name": "search_papers"})


def test_policy_bounds_steps_tools_and_duplicate_calls():
    policy = RunPolicy(max_steps=20, max_steps_per_phase=4, max_tool_repeats=2)

    policy.ensure_step_allowed(total_steps=19, phase_steps=3)
    policy.ensure_tool_allowed("search_papers", {"search_papers"})
    policy.ensure_tool_repeat("same-hash", ["same-hash"])

    with pytest.raises(PolicyViolation, match="run step budget"):
        policy.ensure_step_allowed(total_steps=20, phase_steps=0)
    with pytest.raises(PolicyViolation, match="phase step budget"):
        policy.ensure_step_allowed(total_steps=1, phase_steps=4)
    with pytest.raises(PolicyViolation, match="not allowed"):
        policy.ensure_tool_allowed("export_report", {"search_papers"})
    with pytest.raises(PolicyViolation, match="repeated"):
        policy.ensure_tool_repeat("same-hash", ["same-hash", "same-hash"])


def test_hook_bus_runs_in_order_and_stops_on_pause():
    calls: list[str] = []

    class RecordingHook(AgentHook):
        def __init__(self, name: str, order: int, decision: HookDecision):
            self.name = name
            self.order = order
            self.decision = decision

        async def handle(self, event: str, context: HookContext) -> HookResult:
            calls.append(f"{self.name}:{event}:{context.run_id}")
            return HookResult(decision=self.decision, reason=self.name)

    bus = HookBus(
        [
            RecordingHook("late", 30, HookDecision.ALLOW),
            RecordingHook("first", 10, HookDecision.ALLOW),
            RecordingHook("pause", 20, HookDecision.PAUSE),
        ]
    )
    context = HookContext(run_id="run-1", phase=AgentPhase.PLAN)
    result = asyncio.run(bus.emit("before_step", context))

    assert result.decision == HookDecision.PAUSE
    assert calls == ["first:before_step:run-1", "pause:before_step:run-1"]
    with pytest.raises(ValidationError):
        context.run_id = "mutated"

