from __future__ import annotations

from dataclasses import dataclass

from .schemas import AgentPhase


@dataclass(frozen=True)
class PhaseDefinition:
    phase: AgentPhase
    allowed_tools: tuple[str, ...]
    required_artifacts: tuple[str, ...]
    objective: str


PHASES: dict[AgentPhase, PhaseDefinition] = {
    AgentPhase.PLAN: PhaseDefinition(
        AgentPhase.PLAN,
        ("plan_research",),
        ("research_plan",),
        "明确研究问题并形成检索计划",
    ),
    AgentPhase.DISCOVER: PhaseDefinition(
        AgentPhase.DISCOVER,
        ("search_papers",),
        ("paper_set",),
        "检索并筛选带摘要的相关论文",
    ),
    AgentPhase.EXTRACT: PhaseDefinition(
        AgentPhase.EXTRACT,
        ("extract_claims",),
        ("claim_set",),
        "从论文中抽取结构化核心主张",
    ),
    AgentPhase.ANALYZE: PhaseDefinition(
        AgentPhase.ANALYZE,
        ("classify_relations",),
        ("evidence_graph",),
        "识别主张之间的支持、矛盾与无关关系",
    ),
    AgentPhase.SYNTHESIZE: PhaseDefinition(
        AgentPhase.SYNTHESIZE,
        ("generate_review", "recommend_directions"),
        ("review_draft", "recommendations"),
        "生成学术综述与后续研究方向",
    ),
    AgentPhase.VERIFY: PhaseDefinition(
        AgentPhase.VERIFY,
        ("verify_evidence",),
        ("verification_report",),
        "检查引用覆盖和证据结构一致性",
    ),
    AgentPhase.FINALIZE: PhaseDefinition(
        AgentPhase.FINALIZE,
        ("finalize_report",),
        ("final_report",),
        "封装经过验证的最终报告",
    ),
}

PHASE_ORDER = tuple(PHASES)


def next_phase(phase: AgentPhase) -> AgentPhase | None:
    index = PHASE_ORDER.index(phase)
    if index + 1 >= len(PHASE_ORDER):
        return None
    return PHASE_ORDER[index + 1]

