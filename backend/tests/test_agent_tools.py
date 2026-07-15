from __future__ import annotations

import asyncio
import json

from agent.repository import AgentRepository
from agent.schemas import AgentPhase
from agent.tools import ToolContext, build_default_tool_registry


def test_default_tools_build_a_compatible_final_report(session_factory, monkeypatch):
    import agent.tools as tools_module

    async def fake_search(query, limit=20, refresh=False):
        return (
            [
                {
                    "paperId": "paper-1",
                    "title": "Remote Work Study",
                    "abstract": "Remote work improved measured output.",
                    "year": 2024,
                    "authors": ["A. Researcher"],
                    "citationCount": 12,
                    "doi": "10.1000/demo",
                    "url": "https://example.test/paper-1",
                    "source": "openalex",
                }
            ],
            "2026-07-14T00:00:00+00:00",
        )

    async def fake_extract(abstract):
        return [
            {
                "subject": "software engineers",
                "intervention": "remote work",
                "conclusion": "output improved",
                "direction": "positive",
            }
        ]

    async def fake_matrix(claims, refresh=False):
        return [[{"relation": "unrelated", "confidence": 1.0, "reason": "self"}]]

    async def fake_review(**kwargs):
        return {
            "review": "现有证据表明远程办公可能改善软件工程师的产出[1]。",
            "elapsed_ms": 12,
            "input_tokens": 20,
            "output_tokens": 15,
            "model": "fake-model",
        }

    async def fake_chat(**kwargs):
        return {
            "content": json.dumps(
                {
                    "questions": [
                        {
                            "title": "远程办公效果是否随任务类型变化？",
                            "desc": "比较不同任务复杂度下的产出变化。",
                            "sources": ["核心观点"],
                            "priority": "high",
                        }
                    ],
                    "methods": [],
                },
                ensure_ascii=False,
            ),
            "elapsed_ms": 8,
            "input_tokens": 10,
            "output_tokens": 10,
            "model": "fake-model",
        }

    monkeypatch.setattr(tools_module, "search_papers_domain", fake_search)
    monkeypatch.setattr(tools_module, "extract_claims_domain", fake_extract)
    monkeypatch.setattr(tools_module, "build_matrix_domain", fake_matrix)
    monkeypatch.setattr(tools_module, "generate_review_domain", fake_review)
    monkeypatch.setattr(tools_module, "deepseek_chat_domain", fake_chat)

    repo = AgentRepository(session_factory)
    run = repo.create_run(query="remote work productivity", paper_limit=1)
    registry = build_default_tool_registry()

    async def invoke_and_save(name, arguments, phase):
        execution = await registry.invoke(
            name,
            arguments,
            ToolContext(run_id=run.id, phase=phase, repository=repo),
        )
        assert execution.result.status == "success", execution.result.error_message
        if execution.result.artifact_kind:
            repo.save_artifact(
                run.id,
                execution.result.artifact_kind,
                execution.result.data,
            )
        return execution.result.data

    async def run_pipeline():
        await invoke_and_save(
            "plan_research",
            {"query": run.query, "user_context": []},
            AgentPhase.PLAN,
        )
        await invoke_and_save(
            "search_papers",
            {"query": run.query, "limit": 1, "refresh": False},
            AgentPhase.DISCOVER,
        )
        await invoke_and_save("extract_claims", {}, AgentPhase.EXTRACT)
        await invoke_and_save("classify_relations", {"refresh": False}, AgentPhase.ANALYZE)
        await invoke_and_save("generate_review", {}, AgentPhase.SYNTHESIZE)
        await invoke_and_save("recommend_directions", {}, AgentPhase.SYNTHESIZE)
        verification = await invoke_and_save("verify_evidence", {}, AgentPhase.VERIFY)
        report = await invoke_and_save("finalize_report", {}, AgentPhase.FINALIZE)
        return verification, report

    verification, report = asyncio.run(run_pipeline())

    assert verification["citation_coverage"] == 1.0
    assert report["task_id"] == run.id
    assert report["query"] == "remote work productivity"
    assert len(report["papers"]) == 1
    assert len(report["claims"]) == 1
    assert report["stats"]["papers_count"] == 1
    assert report["review"].startswith("现有证据")
    assert report["recommendations"]["questions"][0]["priority"] == "high"

