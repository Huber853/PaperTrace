from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from .schemas import AgentPhase, RunStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def uuid_hex() -> str:
    return uuid.uuid4().hex


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uuid_hex)
    query: Mapped[str] = mapped_column(String(300))
    paper_limit: Mapped[int] = mapped_column(Integer, default=20)
    refresh: Mapped[bool] = mapped_column(default=False)
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, native_enum=False, values_callable=lambda values: [v.value for v in values]),
        default=RunStatus.QUEUED,
        index=True,
    )
    current_phase: Mapped[AgentPhase] = mapped_column(
        SAEnum(AgentPhase, native_enum=False, values_callable=lambda values: [v.value for v in values]),
        default=AgentPhase.PLAN,
    )
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_context_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    pending_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_agent_runs_status_created", "status", "created_at"),)


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uuid_hex)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    phase: Mapped[AgentPhase] = mapped_column(
        SAEnum(AgentPhase, native_enum=False, values_callable=lambda values: [v.value for v in values])
    )
    action_type: Mapped[str] = mapped_column(String(50))
    action_summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="running")
    model_name: Mapped[str] = mapped_column(String(100), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_agent_step_sequence"),)


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uuid_hex)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[str] = mapped_column(ForeignKey("agent_steps.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(100), index=True)
    arguments_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    arguments_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    artifact_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentArtifact(Base):
    __tablename__ = "agent_artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uuid_hex)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_json: Mapped[Any] = mapped_column(JSON)
    source_step_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("run_id", "kind", "version", name="uq_agent_artifact_version"),
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    phase: Mapped[AgentPhase | None] = mapped_column(
        SAEnum(AgentPhase, native_enum=False, values_callable=lambda values: [v.value for v in values]),
        nullable=True,
    )
    message: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_agent_event_sequence"),)

