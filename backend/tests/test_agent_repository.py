from __future__ import annotations

from agent.repository import AgentRepository
from agent.schemas import AgentPhase, RunStatus


def test_run_lifecycle_persists_events_artifacts_and_input(session_factory):
    repo = AgentRepository(session_factory)
    run = repo.create_run(query="remote work productivity", paper_limit=5)

    assert run.status == RunStatus.QUEUED
    assert run.current_phase == AgentPhase.PLAN

    claimed = repo.claim_next_run()
    assert claimed is not None
    assert claimed.id == run.id
    assert claimed.status == RunStatus.RUNNING

    first = repo.append_event(run.id, "run.started", "开始研究")
    second = repo.append_event(run.id, "phase.started", "开始规划")
    assert [first.sequence, second.sequence] == [1, 2]

    artifact_v1 = repo.save_artifact(run.id, "research_plan", {"queries": ["remote work"]})
    artifact_v2 = repo.save_artifact(run.id, "research_plan", {"queries": ["hybrid work"]})
    assert artifact_v1.version == 1
    assert artifact_v2.version == 2
    assert repo.latest_artifact(run.id, "research_plan").content_json == {
        "queries": ["hybrid work"]
    }

    repo.transition(
        run.id,
        RunStatus.WAITING_INPUT,
        pending_question="需要限定行业吗？",
    )
    resumed = repo.submit_input(run.id, "限定为软件工程师")
    assert resumed.status == RunStatus.QUEUED
    assert resumed.pending_question is None
    assert resumed.input_context_json == ["限定为软件工程师"]

    repo.transition(run.id, RunStatus.RUNNING)
    repo.transition(run.id, RunStatus.FAILED, error_code="provider_error", error_message="timeout")
    retried = repo.retry_run(run.id)
    assert retried.status == RunStatus.QUEUED
    assert retried.error_code is None
    assert retried.error_message is None


def test_recover_running_requeues_only_incomplete_runs(session_factory):
    repo = AgentRepository(session_factory)
    running = repo.create_run(query="running")
    completed = repo.create_run(query="completed")

    repo.transition(running.id, RunStatus.RUNNING)
    repo.transition(completed.id, RunStatus.RUNNING)
    repo.transition(completed.id, RunStatus.COMPLETED)

    assert repo.recover_running() == 1
    assert repo.get_run(running.id).status == RunStatus.QUEUED
    assert repo.get_run(completed.id).status == RunStatus.COMPLETED

