from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import TypeAdapter

from .phases import PhaseDefinition
from .schemas import AbortRun, AgentAction, AgentPhase


@dataclass(frozen=True)
class ModelTurn:
    action: AgentAction
    model_name: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class ModelProvider(Protocol):
    async def next_action(
        self,
        *,
        run: Any,
        phase: AgentPhase,
        definition: PhaseDefinition,
        artifacts: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        observations: list[str],
    ) -> ModelTurn: ...


class ScriptedModelProvider:
    """Deterministic provider for tests and offline evaluations."""

    def __init__(self, scripts: dict[AgentPhase, list[AgentAction]]):
        self._scripts = {phase: deque(actions) for phase, actions in scripts.items()}

    async def next_action(self, *, phase: AgentPhase, **kwargs) -> ModelTurn:
        actions = self._scripts.get(phase)
        if not actions:
            return ModelTurn(
                AbortRun(
                    error_code="script_exhausted",
                    message=f"no scripted action remains for {phase.value}",
                ),
                model_name="scripted",
            )
        return ModelTurn(actions.popleft(), model_name="scripted")


class DeepSeekModelProvider:
    def __init__(self, chat_callable=None):
        self._adapter = TypeAdapter(AgentAction)
        self._chat_callable = chat_callable

    async def next_action(
        self,
        *,
        run,
        phase: AgentPhase,
        definition: PhaseDefinition,
        artifacts: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        observations: list[str],
    ) -> ModelTurn:
        if self._chat_callable is None:
            from generator import deepseek_chat

            chat_callable = deepseek_chat
        else:
            chat_callable = self._chat_callable

        prompt = {
            "research_question": run.query,
            "user_context": run.input_context_json or [],
            "phase": phase.value,
            "objective": definition.objective,
            "required_artifacts": list(definition.required_artifacts),
            "available_artifacts": artifacts,
            "available_tools": tools,
            "recent_observations": observations[-4:],
        }
        response = await chat_callable(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是 PaperTrace 研究 Agent 的受限执行器。每轮只返回一个 JSON 动作。"
                        "action 只能是 tool、complete_phase、request_input、abort_run。"
                        "调用工具时 arguments 必须符合工具 schema；只有必需产物都存在时才能完成阶段。"
                        "rationale 只写一句可审计决策摘要，不输出详细思维链。"
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            timeout_s=45.0,
        )
        action = self._adapter.validate_json(response["content"])
        return ModelTurn(
            action=action,
            model_name=response.get("model", ""),
            input_tokens=int(response.get("input_tokens", 0)),
            output_tokens=int(response.get("output_tokens", 0)),
        )
