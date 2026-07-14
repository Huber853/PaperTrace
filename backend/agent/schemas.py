from __future__ import annotations

from enum import Enum


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentPhase(str, Enum):
    PLAN = "plan"
    DISCOVER = "discover"
    EXTRACT = "extract"
    ANALYZE = "analyze"
    SYNTHESIZE = "synthesize"
    VERIFY = "verify"
    FINALIZE = "finalize"

