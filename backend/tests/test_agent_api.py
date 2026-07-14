from __future__ import annotations

import asyncio
import os

from fastapi.testclient import TestClient

from agent.repository import AgentRepository
from agent.schemas import AgentPhase, RunStatus
from agent.worker import AgentWorker


os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ["AGENT_EMBEDDED_WORKER"] = "false"

import main  # noqa: E402


def test_agent_api_exposes_run_events_trace_and_controls(session_factory, monkeypatch):
    repo = AgentRepository(session_factory)
    monkeypatch.setattr(main, "AGENT_REPOSITORY", repo, raising=False)

    with TestClient(main.app) as client:
        created = client.post(
            "/api/agent/runs",
            json={"query": "remote work", "limit": 3, "refresh": False},
        )
        assert created.status_code == 200
        run_id = created.json()["run_id"]
        assert created.json()["status"] == "queued"

        repo.append_event(run_id, "phase.started", "开始规划", phase=AgentPhase.PLAN)
        repo.append_step(
            run_id,
            AgentPhase.PLAN,
            "tool",
            "形成研究计划",
            status="completed",
        )

        status_response = client.get(f"/api/agent/runs/{run_id}")
        assert status_response.status_code == 200
        assert status_response.json()["current_phase"] == "plan"

        events = client.get(f"/api/agent/runs/{run_id}/events?after_seq=0")
        assert events.json()["events"][0]["message"] == "开始规划"
        trace = client.get(f"/api/agent/runs/{run_id}/trace")
        assert trace.json()["steps"][0]["action_summary"] == "形成研究计划"

        repo.transition(run_id, RunStatus.RUNNING)
        repo.transition(
            run_id,
            RunStatus.WAITING_INPUT,
            pending_question="请限定人群",
        )
        submitted = client.post(
            f"/api/agent/runs/{run_id}/input",
            json={"content": "软件工程师"},
        )
        assert submitted.json()["status"] == "queued"

        cancelled = client.post(f"/api/agent/runs/{run_id}/cancel")
        assert cancelled.json()["status"] == "cancelled"


def test_legacy_endpoints_read_persistent_final_artifact(session_factory, monkeypatch):
    repo = AgentRepository(session_factory)
    monkeypatch.setattr(main, "AGENT_REPOSITORY", repo, raising=False)

    result = {
        "task_id": "placeholder",
        "query": "remote work",
        "papers": [],
        "claims": [],
        "matrix": [],
        "stats": {
            "papers_count": 0,
            "claims_count": 0,
            "contradict_pairs": 0,
            "support_pairs": 0,
        },
        "timeline": [],
        "data_fetched_at": "2026-07-14T00:00:00+00:00",
        "review": "持久化综述",
        "review_meta": {
            "elapsed_ms": 1,
            "input_tokens": 2,
            "output_tokens": 3,
            "model": "fake",
        },
        "recommendations": {"questions": [], "methods": []},
        "verification": {"grounded": True},
    }

    with TestClient(main.app) as client:
        response = client.post(
            "/api/analyze",
            json={"query": "remote work", "limit": 1},
        )
        assert response.status_code == 200
        run_id = response.json()["task_id"]
        result["task_id"] = run_id

        repo.transition(run_id, RunStatus.RUNNING)
        repo.save_artifact(run_id, "final_report", result)
        repo.transition(run_id, RunStatus.COMPLETED)

        legacy_status = client.get(f"/api/task/{run_id}")
        assert legacy_status.json()["status"] == "done"
        legacy_result = client.get(f"/api/result/{run_id}")
        assert legacy_result.json()["query"] == "remote work"
        review = client.get(f"/api/review/{run_id}")
        assert review.json()["review"] == "持久化综述"
        export = client.post(f"/api/export/{run_id}?format=md", json={})
        assert export.status_code == 200
        assert "PaperTrace" in export.text


def test_failed_run_can_be_retried_through_api(session_factory, monkeypatch):
    repo = AgentRepository(session_factory)
    monkeypatch.setattr(main, "AGENT_REPOSITORY", repo, raising=False)
    run = repo.create_run(query="retry")
    repo.transition(run.id, RunStatus.RUNNING)
    repo.transition(
        run.id,
        RunStatus.FAILED,
        error_code="provider_error",
        error_message="timeout",
    )

    with TestClient(main.app) as client:
        response = client.post(f"/api/agent/runs/{run.id}/retry")
        assert response.status_code == 200
        assert response.json()["status"] == "queued"


def test_worker_claims_one_queued_run(session_factory):
    repo = AgentRepository(session_factory)
    run = repo.create_run(query="worker test")
    executed: list[str] = []

    class FakeHarness:
        async def run(self, run_id):
            executed.append(run_id)
            repo.transition(run_id, RunStatus.COMPLETED)

    worker = AgentWorker(repo, harness_factory=lambda: FakeHarness())

    assert asyncio.run(worker.run_once()) is True
    assert executed == [run.id]
    assert repo.get_run(run.id).status == RunStatus.COMPLETED
    assert asyncio.run(worker.run_once()) is False
