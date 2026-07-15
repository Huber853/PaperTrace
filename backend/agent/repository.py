from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from database import SessionLocal
from .models import AgentArtifact, AgentEvent, AgentRun, AgentStep, AgentToolCall
from .schemas import AgentPhase, RunStatus


TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}

ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.QUEUED,
        RunStatus.WAITING_INPUT,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.WAITING_INPUT: {RunStatus.QUEUED, RunStatus.CANCELLED},
    RunStatus.FAILED: {RunStatus.QUEUED},
    RunStatus.COMPLETED: set(),
    RunStatus.CANCELLED: set(),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentRepository:
    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal):
        self.session_factory = session_factory

    @contextmanager
    def session(self) -> Iterator[Session]:
        db = self.session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create_run(
        self,
        query: str,
        paper_limit: int = 20,
        refresh: bool = False,
        policy_json: dict[str, Any] | None = None,
    ) -> AgentRun:
        with self.session() as db:
            run = AgentRun(
                query=query,
                paper_limit=paper_limit,
                refresh=refresh,
                policy_json=policy_json or {},
            )
            db.add(run)
            db.flush()
            return run

    def get_run(self, run_id: str) -> AgentRun | None:
        with self.session() as db:
            return db.get(AgentRun, run_id)

    def claim_next_run(self) -> AgentRun | None:
        with self.session() as db:
            run = db.scalar(
                select(AgentRun)
                .where(AgentRun.status == RunStatus.QUEUED)
                .order_by(AgentRun.created_at, AgentRun.id)
                .limit(1)
            )
            if run is None:
                return None
            run.status = RunStatus.RUNNING
            run.started_at = run.started_at or utc_now()
            run.updated_at = utc_now()
            db.flush()
            return run

    def transition(self, run_id: str, status: RunStatus, **fields: Any) -> AgentRun:
        with self.session() as db:
            run = self._require_run(db, run_id)
            if status != run.status and status not in ALLOWED_TRANSITIONS[run.status]:
                raise ValueError(f"invalid run transition: {run.status.value} -> {status.value}")
            run.status = status
            run.updated_at = utc_now()
            if status in TERMINAL_STATUSES:
                run.finished_at = utc_now()
            for key, value in fields.items():
                if not hasattr(run, key):
                    raise AttributeError(f"unknown AgentRun field: {key}")
                setattr(run, key, value)
            db.flush()
            return run

    def append_event(
        self,
        run_id: str,
        event_type: str,
        message: str = "",
        *,
        phase: AgentPhase | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AgentEvent:
        with self.session() as db:
            self._require_run(db, run_id)
            last = db.scalar(
                select(func.max(AgentEvent.sequence)).where(AgentEvent.run_id == run_id)
            ) or 0
            event = AgentEvent(
                run_id=run_id,
                sequence=last + 1,
                event_type=event_type,
                phase=phase,
                message=message,
                payload_json=payload or {},
            )
            db.add(event)
            db.flush()
            return event

    def list_events(self, run_id: str, after_sequence: int = 0) -> list[AgentEvent]:
        with self.session() as db:
            return list(
                db.scalars(
                    select(AgentEvent)
                    .where(AgentEvent.run_id == run_id, AgentEvent.sequence > after_sequence)
                    .order_by(AgentEvent.sequence)
                )
            )

    def save_artifact(
        self,
        run_id: str,
        kind: str,
        content: Any,
        source_step_id: str | None = None,
    ) -> AgentArtifact:
        with self.session() as db:
            self._require_run(db, run_id)
            last = db.scalar(
                select(func.max(AgentArtifact.version)).where(
                    AgentArtifact.run_id == run_id,
                    AgentArtifact.kind == kind,
                )
            ) or 0
            artifact = AgentArtifact(
                run_id=run_id,
                kind=kind,
                version=last + 1,
                content_json=content,
                source_step_id=source_step_id,
            )
            db.add(artifact)
            db.flush()
            return artifact

    def latest_artifact(self, run_id: str, kind: str) -> AgentArtifact | None:
        with self.session() as db:
            return db.scalar(
                select(AgentArtifact)
                .where(AgentArtifact.run_id == run_id, AgentArtifact.kind == kind)
                .order_by(AgentArtifact.version.desc())
                .limit(1)
            )

    def list_artifacts(self, run_id: str) -> list[AgentArtifact]:
        with self.session() as db:
            return list(
                db.scalars(
                    select(AgentArtifact)
                    .where(AgentArtifact.run_id == run_id)
                    .order_by(AgentArtifact.created_at, AgentArtifact.version)
                )
            )

    def get_artifact(self, artifact_id: str) -> AgentArtifact | None:
        with self.session() as db:
            return db.get(AgentArtifact, artifact_id)

    def append_step(
        self,
        run_id: str,
        phase: AgentPhase,
        action_type: str,
        action_summary: str,
        **fields: Any,
    ) -> AgentStep:
        with self.session() as db:
            run = self._require_run(db, run_id)
            last = db.scalar(
                select(func.max(AgentStep.sequence)).where(AgentStep.run_id == run_id)
            ) or 0
            step_fields = dict(fields)
            if step_fields.get("status") == "completed" and "finished_at" not in step_fields:
                step_fields["finished_at"] = utc_now()
            step = AgentStep(
                run_id=run_id,
                sequence=last + 1,
                phase=phase,
                action_type=action_type,
                action_summary=action_summary,
                **step_fields,
            )
            run.step_count = last + 1
            run.token_usage += int(step.input_tokens or 0) + int(step.output_tokens or 0)
            db.add(step)
            db.flush()
            return step

    def append_tool_call(self, **fields: Any) -> AgentToolCall:
        with self.session() as db:
            call = AgentToolCall(**fields)
            db.add(call)
            db.flush()
            return call

    def list_tool_calls(self, run_id: str) -> list[AgentToolCall]:
        with self.session() as db:
            return list(
                db.scalars(
                    select(AgentToolCall)
                    .where(AgentToolCall.run_id == run_id)
                    .order_by(AgentToolCall.created_at, AgentToolCall.id)
                )
            )

    def find_successful_tool_call(
        self,
        run_id: str,
        tool_name: str,
        arguments_hash: str,
    ) -> AgentToolCall | None:
        with self.session() as db:
            return db.scalar(
                select(AgentToolCall)
                .where(
                    AgentToolCall.run_id == run_id,
                    AgentToolCall.tool_name == tool_name,
                    AgentToolCall.arguments_hash == arguments_hash,
                    AgentToolCall.status.in_(["success", "partial"]),
                )
                .order_by(AgentToolCall.created_at.desc())
                .limit(1)
            )

    def list_steps(self, run_id: str) -> list[AgentStep]:
        with self.session() as db:
            return list(
                db.scalars(
                    select(AgentStep)
                    .where(AgentStep.run_id == run_id)
                    .order_by(AgentStep.sequence)
                )
            )

    def submit_input(self, run_id: str, user_input: str) -> AgentRun:
        with self.session() as db:
            run = self._require_run(db, run_id)
            if run.status != RunStatus.WAITING_INPUT:
                raise ValueError("run is not waiting for input")
            run.input_context_json = [*(run.input_context_json or []), user_input]
            run.pending_question = None
            run.status = RunStatus.QUEUED
            run.updated_at = utc_now()
            db.flush()
            return run

    def retry_run(self, run_id: str) -> AgentRun:
        with self.session() as db:
            run = self._require_run(db, run_id)
            if run.status != RunStatus.FAILED:
                raise ValueError("only failed runs can be retried")
            run.status = RunStatus.QUEUED
            run.error_code = None
            run.error_message = None
            run.finished_at = None
            run.updated_at = utc_now()
            db.flush()
            return run

    def recover_running(self) -> int:
        with self.session() as db:
            runs = list(db.scalars(select(AgentRun).where(AgentRun.status == RunStatus.RUNNING)))
            for run in runs:
                run.status = RunStatus.QUEUED
                run.updated_at = utc_now()
            return len(runs)

    @staticmethod
    def _require_run(db: Session, run_id: str) -> AgentRun:
        run = db.get(AgentRun, run_id)
        if run is None:
            raise KeyError(f"agent run not found: {run_id}")
        return run
