from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .hooks import BudgetHook, HookBus, HookContext, HookDecision, ProgressHook
from .model_provider import ModelProvider
from .phases import PhaseDefinition
from .policies import PolicyViolation, RunPolicy
from .repository import AgentRepository
from .schemas import (
    AbortRun,
    CompletePhase,
    RequestInput,
    RunStatus,
    ToolAction,
)
from .tools import ToolContext, ToolRegistry


@dataclass(frozen=True)
class LoopOutcome:
    status: Literal["completed", "paused", "failed", "cancelled"]
    reason: str = ""


class AgentLoop:
    def __init__(
        self,
        repository: AgentRepository,
        registry: ToolRegistry,
        model_provider: ModelProvider,
        policy: RunPolicy,
        hook_bus: HookBus | None = None,
    ):
        self.repository = repository
        self.registry = registry
        self.model_provider = model_provider
        self.policy = policy
        self.hook_bus = hook_bus or HookBus(
            [BudgetHook(policy), ProgressHook(repository)]
        )

    async def run_phase(self, run_id: str, definition: PhaseDefinition) -> LoopOutcome:
        observations: list[str] = []
        phase = definition.phase

        while True:
            run = self.repository.get_run(run_id)
            if run is None:
                return LoopOutcome("failed", "run not found")
            if run.status == RunStatus.CANCELLED:
                return LoopOutcome("cancelled", "run cancelled")

            steps = self.repository.list_steps(run_id)
            phase_steps = sum(step.phase == phase for step in steps)
            hook_result = await self.hook_bus.emit(
                "before_step",
                HookContext(
                    run_id=run_id,
                    phase=phase,
                    payload={
                        "total_steps": len(steps),
                        "phase_steps": phase_steps,
                    },
                ),
            )
            if hook_result.decision != HookDecision.ALLOW:
                return LoopOutcome("failed", hook_result.reason or "step rejected")

            artifacts = self.repository.list_artifacts(run_id)
            turn = await self.model_provider.next_action(
                run=run,
                phase=phase,
                definition=definition,
                artifacts=[
                    {"id": item.id, "kind": item.kind, "version": item.version}
                    for item in artifacts
                ],
                tools=self.registry.definitions_for_phase(phase),
                observations=observations,
            )
            action = turn.action
            summary = _action_summary(action)
            step = self.repository.append_step(
                run_id,
                phase,
                action.action,
                summary,
                status="completed",
                model_name=turn.model_name,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
            )

            if isinstance(action, CompletePhase):
                missing = [
                    kind
                    for kind in definition.required_artifacts
                    if self.repository.latest_artifact(run_id, kind) is None
                ]
                if missing:
                    observations.append(f"阶段不能完成，缺少产物: {', '.join(missing)}")
                    continue
                self.repository.append_event(
                    run_id,
                    "phase.completed",
                    action.summary,
                    phase=phase,
                )
                return LoopOutcome("completed")

            if isinstance(action, RequestInput):
                self.repository.transition(
                    run_id,
                    RunStatus.WAITING_INPUT,
                    pending_question=action.question,
                )
                self.repository.append_event(
                    run_id,
                    "run.waiting_input",
                    action.question,
                    phase=phase,
                    payload={"reason": action.reason},
                )
                return LoopOutcome("paused", action.reason)

            if isinstance(action, AbortRun):
                self.repository.transition(
                    run_id,
                    RunStatus.FAILED,
                    error_code=action.error_code,
                    error_message=action.message,
                )
                return LoopOutcome("failed", action.message)

            if isinstance(action, ToolAction):
                try:
                    self.policy.ensure_tool_allowed(
                        action.tool_name,
                        set(definition.allowed_tools),
                    )
                    call_hash = self.registry.arguments_hash(
                        action.tool_name,
                        action.arguments,
                    )
                    previous_calls = self.repository.list_tool_calls(run_id)
                    self.policy.ensure_tool_repeat(
                        call_hash,
                        [call.arguments_hash for call in previous_calls],
                    )
                except PolicyViolation as exc:
                    return LoopOutcome("failed", str(exc))

                reused = self.repository.find_successful_tool_call(
                    run_id,
                    action.tool_name,
                    call_hash,
                )
                if reused is not None:
                    observations.append(f"复用工具结果: {action.tool_name}")
                    self.repository.append_event(
                        run_id,
                        "tool.reused",
                        f"复用 {action.tool_name}",
                        phase=phase,
                    )
                    continue

                before_tool = await self.hook_bus.emit(
                    "before_tool",
                    HookContext(
                        run_id=run_id,
                        phase=phase,
                        step_id=step.id,
                        payload={
                            "tool_name": action.tool_name,
                            "allowed_tools": list(definition.allowed_tools),
                        },
                    ),
                )
                if before_tool.decision != HookDecision.ALLOW:
                    return LoopOutcome("failed", before_tool.reason or "tool rejected")

                execution = await self.registry.invoke(
                    action.tool_name,
                    action.arguments,
                    ToolContext(run_id=run_id, phase=phase, repository=self.repository),
                    retry_count=self.policy.tool_retry_count,
                )
                artifact_ids: list[str] = []
                if (
                    execution.result.status in {"success", "partial"}
                    and execution.result.artifact_kind
                ):
                    artifact = self.repository.save_artifact(
                        run_id,
                        execution.result.artifact_kind,
                        execution.result.data,
                        source_step_id=step.id,
                    )
                    artifact_ids.append(artifact.id)
                    self.repository.append_event(
                        run_id,
                        "artifact.saved",
                        f"生成 {artifact.kind}",
                        phase=phase,
                        payload={"artifact_id": artifact.id, "kind": artifact.kind},
                    )

                self.repository.append_tool_call(
                    run_id=run_id,
                    step_id=step.id,
                    tool_name=execution.tool_name,
                    arguments_json=execution.arguments,
                    arguments_hash=execution.arguments_hash,
                    status=execution.result.status,
                    result_summary=execution.result.summary,
                    artifact_ids_json=artifact_ids,
                    duration_ms=int(execution.result.metrics.get("duration_ms", 0)),
                    retry_count=max(0, execution.attempts - 1),
                    error_code=execution.result.error_code,
                    error_message=execution.result.error_message,
                )
                if execution.result.status == "failed":
                    observations.append(
                        f"工具 {action.tool_name} 失败: {execution.result.error_message}"
                    )
                else:
                    observations.append(
                        execution.result.summary or f"工具 {action.tool_name} 完成"
                    )


def _action_summary(action) -> str:
    if isinstance(action, ToolAction):
        return action.rationale
    if isinstance(action, CompletePhase):
        return action.summary
    if isinstance(action, RequestInput):
        return action.reason
    if isinstance(action, AbortRun):
        return action.message
    return action.action

