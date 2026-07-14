from __future__ import annotations

from .hooks import HookBus
from .loop import AgentLoop
from .model_provider import ModelProvider
from .phases import PHASE_ORDER, PHASES, next_phase
from .policies import RunPolicy
from .repository import AgentRepository
from .schemas import RunStatus
from .tools import ToolRegistry


class AgentHarness:
    def __init__(
        self,
        repository: AgentRepository,
        registry: ToolRegistry,
        model_provider: ModelProvider,
        policy: RunPolicy | None = None,
        hook_bus: HookBus | None = None,
    ):
        self.repository = repository
        self.policy = policy or RunPolicy()
        self.loop = AgentLoop(
            repository,
            registry,
            model_provider,
            self.policy,
            hook_bus,
        )

    async def run(self, run_id: str):
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(f"agent run not found: {run_id}")
        if run.status == RunStatus.QUEUED:
            run = self.repository.transition(run_id, RunStatus.RUNNING)
        elif run.status != RunStatus.RUNNING:
            return run

        self.repository.append_event(
            run_id,
            "run.started",
            "Agent 开始执行",
            phase=run.current_phase,
        )

        try:
            start = PHASE_ORDER.index(run.current_phase)
            for phase in PHASE_ORDER[start:]:
                definition = PHASES[phase]
                run = self.repository.get_run(run_id)
                if run.status == RunStatus.CANCELLED:
                    return run
                self.repository.transition(
                    run_id,
                    RunStatus.RUNNING,
                    current_phase=phase,
                )

                if all(
                    self.repository.latest_artifact(run_id, kind) is not None
                    for kind in definition.required_artifacts
                ):
                    self.repository.append_event(
                        run_id,
                        "phase.recovered",
                        f"复用已完成阶段 {phase.value}",
                        phase=phase,
                    )
                else:
                    self.repository.append_event(
                        run_id,
                        "phase.started",
                        definition.objective,
                        phase=phase,
                    )
                    outcome = await self.loop.run_phase(run_id, definition)
                    if outcome.status in {"paused", "cancelled"}:
                        return self.repository.get_run(run_id)
                    if outcome.status == "failed":
                        current = self.repository.get_run(run_id)
                        if current.status == RunStatus.RUNNING:
                            self.repository.transition(
                                run_id,
                                RunStatus.FAILED,
                                error_code="policy_exceeded",
                                error_message=outcome.reason,
                            )
                        return self.repository.get_run(run_id)

                following = next_phase(phase)
                if following is not None:
                    self.repository.transition(
                        run_id,
                        RunStatus.RUNNING,
                        current_phase=following,
                    )

            completed = self.repository.transition(run_id, RunStatus.COMPLETED)
            self.repository.append_event(
                run_id,
                "run.completed",
                "分析完成",
                phase=completed.current_phase,
            )
            return completed
        except Exception as exc:
            current = self.repository.get_run(run_id)
            if current is not None and current.status == RunStatus.RUNNING:
                self.repository.transition(
                    run_id,
                    RunStatus.FAILED,
                    error_code="internal_error",
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            self.repository.append_event(
                run_id,
                "run.failed",
                f"{type(exc).__name__}: {exc}",
                phase=current.current_phase if current else PHASE_ORDER[0],
            )
            return self.repository.get_run(run_id)

