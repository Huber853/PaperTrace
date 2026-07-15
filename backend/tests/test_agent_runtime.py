from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError
from pydantic import BaseModel

from agent.hooks import AgentHook, HookBus, HookContext, HookDecision, HookResult
from agent.policies import PolicyViolation, RunPolicy
from agent.schemas import AgentAction, AgentPhase, CompletePhase, ToolAction
from agent.tools import AgentTool, ToolContext, ToolRegistry
from agent.harness import AgentHarness
from agent.model_provider import DeepSeekModelProvider, ScriptedModelProvider
from agent.schemas import RequestInput, RunStatus, ToolResult


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


def test_tool_registry_validates_phase_hashes_and_retries():
    class EchoInput(BaseModel):
        text: str

    class EchoOutput(BaseModel):
        echoed: str

    class FlakyEchoTool(AgentTool):
        name = "echo"
        description = "Echo a value"
        input_model = EchoInput
        output_model = EchoOutput
        allowed_phases = {AgentPhase.PLAN}

        def __init__(self):
            self.attempts = 0

        async def execute(self, context, arguments):
            self.attempts += 1
            if self.attempts == 1:
                raise TimeoutError("temporary")
            return {"echoed": arguments.text}

    tool = FlakyEchoTool()
    registry = ToolRegistry([tool])
    context = ToolContext(run_id="run-1", phase=AgentPhase.PLAN)

    execution = asyncio.run(
        registry.invoke("echo", {"text": "hello"}, context, retry_count=1)
    )
    same_hash = registry.arguments_hash("echo", {"text": "hello"})

    assert execution.result.status == "success"
    assert execution.result.data == {"echoed": "hello"}
    assert execution.arguments_hash == same_hash
    assert execution.attempts == 2

    wrong_phase = ToolContext(run_id="run-1", phase=AgentPhase.FINALIZE)
    with pytest.raises(PolicyViolation, match="not allowed"):
        asyncio.run(registry.invoke("echo", {"text": "hello"}, wrong_phase))

    invalid = asyncio.run(registry.invoke("echo", {}, context))
    assert invalid.result.status == "failed"
    assert invalid.result.error_code == "validation_error"


def test_harness_runs_all_phases_and_persists_trace(session_factory):
    from agent.repository import AgentRepository
    from agent.phases import PHASES

    class NoArgs(BaseModel):
        pass

    class ArtifactOutput(BaseModel):
        value: str

    class ArtifactTool(AgentTool):
        input_model = NoArgs
        output_model = ArtifactOutput

        def __init__(self, name, phase, artifact_kind):
            self.name = name
            self.description = name
            self.allowed_phases = {phase}
            self.artifact_kind = artifact_kind

        async def execute(self, context, arguments):
            return ToolResult(
                status="success",
                data={"value": self.artifact_kind},
                summary=f"created {self.artifact_kind}",
                artifact_kind=self.artifact_kind,
            )

    tools = []
    scripts = {}
    for phase, definition in PHASES.items():
        phase_actions = []
        for tool_name, artifact_kind in zip(
            definition.allowed_tools,
            definition.required_artifacts,
            strict=True,
        ):
            tools.append(ArtifactTool(tool_name, phase, artifact_kind))
            phase_actions.append(
                ToolAction(
                    tool_name=tool_name,
                    arguments={},
                    rationale=f"执行 {tool_name}",
                )
            )
        phase_actions.append(
            CompletePhase(summary=f"完成 {phase.value}", artifact_ids=["latest"])
        )
        scripts[phase] = phase_actions

    repo = AgentRepository(session_factory)
    run = repo.create_run(query="agent harness test")
    harness = AgentHarness(
        repository=repo,
        registry=ToolRegistry(tools),
        model_provider=ScriptedModelProvider(scripts),
    )

    completed = asyncio.run(harness.run(run.id))

    assert completed.status == RunStatus.COMPLETED
    assert repo.latest_artifact(run.id, "final_report") is not None
    expected_steps = sum(
        len(definition.required_artifacts) + 1 for definition in PHASES.values()
    )
    assert len(repo.list_steps(run.id)) == expected_steps
    assert any(event.event_type == "run.completed" for event in repo.list_events(run.id))


def test_harness_can_pause_for_user_input(session_factory):
    from agent.repository import AgentRepository

    repo = AgentRepository(session_factory)
    run = repo.create_run(query="ambiguous topic")
    provider = ScriptedModelProvider(
        {
            AgentPhase.PLAN: [
                RequestInput(question="请限定研究人群", reason="主题范围过宽")
            ]
        }
    )
    harness = AgentHarness(repo, ToolRegistry(), provider)

    paused = asyncio.run(harness.run(run.id))

    assert paused.status == RunStatus.WAITING_INPUT
    assert paused.pending_question == "请限定研究人群"


def test_harness_fails_cleanly_when_phase_budget_is_exhausted(session_factory):
    from agent.repository import AgentRepository

    class NoArgs(BaseModel):
        pass

    class PlanTool(AgentTool):
        name = "plan_research"
        description = "plan"
        input_model = NoArgs
        output_model = NoArgs
        allowed_phases = {AgentPhase.PLAN}
        artifact_kind = "research_plan"

        async def execute(self, context, arguments):
            return ToolResult(
                status="success",
                data={},
                summary="planned",
                artifact_kind="research_plan",
            )

    repo = AgentRepository(session_factory)
    run = repo.create_run(query="budget test")
    provider = ScriptedModelProvider(
        {
            AgentPhase.PLAN: [
                ToolAction(tool_name="plan_research", arguments={}, rationale="plan"),
                ToolAction(tool_name="plan_research", arguments={}, rationale="repeat"),
            ]
        }
    )
    harness = AgentHarness(
        repo,
        ToolRegistry([PlanTool()]),
        provider,
        policy=RunPolicy(max_steps_per_phase=1),
    )

    failed = asyncio.run(harness.run(run.id))

    assert failed.status == RunStatus.FAILED
    assert failed.error_code == "policy_exceeded"


def test_deepseek_provider_parses_structured_action_without_loading_secrets():
    from agent.phases import PHASES

    async def fake_chat(**kwargs):
        return {
            "content": (
                '{"action":"tool","tool_name":"plan_research",'
                '"arguments":{"query":"remote work","user_context":[]},'
                '"rationale":"先形成检索计划"}'
            ),
            "model": "fake-deepseek",
            "input_tokens": 11,
            "output_tokens": 7,
        }

    provider = DeepSeekModelProvider(chat_callable=fake_chat)
    turn = asyncio.run(
        provider.next_action(
            run=SimpleNamespace(query="remote work", input_context_json=[]),
            phase=AgentPhase.PLAN,
            definition=PHASES[AgentPhase.PLAN],
            artifacts=[],
            tools=[],
            observations=[],
        )
    )

    assert isinstance(turn.action, ToolAction)
    assert turn.model_name == "fake-deepseek"
    assert turn.input_tokens == 11
    assert turn.output_tokens == 7
