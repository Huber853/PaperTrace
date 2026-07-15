from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agent.model_provider import ScriptedModelProvider
from agent.phases import PHASES
from agent.schemas import CompletePhase, ToolAction, ToolResult
from agent.tools import AgentTool, ToolRegistry


class EvalInput(BaseModel):
    model_config = ConfigDict(extra="allow")


class EvalOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class EvalArtifactTool(AgentTool):
    input_model = EvalInput
    output_model = EvalOutput

    def __init__(self, name, phase, artifact_kind, query):
        self.name = name
        self.description = f"offline {name}"
        self.allowed_phases = {phase}
        self.artifact_kind = artifact_kind
        self.query = query

    async def execute(self, context, arguments):
        return ToolResult(
            status="success",
            data=_artifact_payload(self.artifact_kind, context.run_id, self.query),
            summary=f"offline {self.artifact_kind}",
            artifact_kind=self.artifact_kind,
        )


def build_offline_runtime(query: str):
    tools = []
    scripts = {}
    for phase, definition in PHASES.items():
        actions = []
        for tool_name, artifact_kind in zip(
            definition.allowed_tools,
            definition.required_artifacts,
            strict=True,
        ):
            tools.append(EvalArtifactTool(tool_name, phase, artifact_kind, query))
            actions.append(
                ToolAction(
                    tool_name=tool_name,
                    arguments={},
                    rationale=f"offline {tool_name}",
                )
            )
        actions.append(
            CompletePhase(
                summary=f"offline {phase.value} complete",
                artifact_ids=["latest"],
            )
        )
        scripts[phase] = actions
    return ToolRegistry(tools), ScriptedModelProvider(scripts)


def _artifact_payload(kind: str, run_id: str, query: str):
    paper = {
        "id": 1,
        "paper_id": "offline-paper-1",
        "title": "Offline Evaluation Paper",
        "abstract": "A deterministic paper used by the smoke evaluation.",
        "year": 2024,
        "authors": ["PaperTrace Eval"],
        "citation_count": 1,
        "doi": None,
        "url": None,
        "source": "offline",
    }
    claim = {
        "id": 1,
        "paper_id": 1,
        "subject": "research agents",
        "intervention": "persistent harness",
        "conclusion": "execution becomes auditable",
        "direction": "positive",
    }
    matrix = [[{"relation": "unrelated", "confidence": 1.0, "reason": "self"}]]
    if kind == "research_plan":
        return {"question": query, "search_queries": [query], "user_context": []}
    if kind == "paper_set":
        return {"papers": [paper], "data_fetched_at": "2026-07-14T00:00:00+00:00"}
    if kind == "claim_set":
        return {"claims": [claim], "errors": []}
    if kind == "evidence_graph":
        return {
            "claims": [claim],
            "matrix": matrix,
            "stats": {
                "papers_count": 1,
                "claims_count": 1,
                "contradict_pairs": 0,
                "support_pairs": 0,
            },
            "timeline": [
                {"year": 2024, "positive": 1, "negative": 0, "neutral": 0, "total": 1}
            ],
        }
    if kind == "review_draft":
        return {
            "review": "持久化 Harness 使研究 Agent 的执行过程可审计[1]。",
            "elapsed_ms": 1,
            "input_tokens": 1,
            "output_tokens": 1,
            "model": "offline",
        }
    if kind == "recommendations":
        return {"questions": [], "methods": [], "meta": {"model": "offline"}}
    if kind == "verification_report":
        return {
            "citation_coverage": 1.0,
            "cited_papers": [1],
            "matrix_valid": True,
            "grounded": True,
            "warnings": [],
        }
    if kind == "final_report":
        return {
            "task_id": run_id,
            "query": query,
            "papers": [paper],
            "claims": [claim],
            "matrix": matrix,
            "stats": {
                "papers_count": 1,
                "claims_count": 1,
                "contradict_pairs": 0,
                "support_pairs": 0,
            },
            "timeline": [
                {"year": 2024, "positive": 1, "negative": 0, "neutral": 0, "total": 1}
            ],
            "data_fetched_at": "2026-07-14T00:00:00+00:00",
            "review": "持久化 Harness 使研究 Agent 的执行过程可审计[1]。",
            "review_meta": {"model": "offline"},
            "recommendations": {"questions": [], "methods": []},
            "verification": {"grounded": True, "citation_coverage": 1.0},
        }
    raise ValueError(f"unsupported offline artifact: {kind}")
