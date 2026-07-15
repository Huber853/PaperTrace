from __future__ import annotations

import hashlib
import json
import asyncio
import re
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .policies import PolicyViolation
from .schemas import AgentPhase, ToolResult


async def search_papers_domain(query: str, limit: int = 20, refresh: bool = False):
    from fetcher import search_papers

    return await search_papers(query, limit=limit, refresh=refresh)


async def extract_claims_domain(abstract: str):
    from extractor import extract_claims

    return await extract_claims(abstract)


async def build_matrix_domain(claims: list[dict], refresh: bool = False):
    from contradiction import build_matrix

    return await build_matrix(claims, refresh=refresh)


async def generate_review_domain(**kwargs):
    from generator import generate_review

    return await generate_review(**kwargs)


async def deepseek_chat_domain(**kwargs):
    from generator import deepseek_chat

    return await deepseek_chat(**kwargs)


def build_timeline_domain(claims: list[dict], paper_year_by_id: dict[int, int | None]):
    from timeline import build_timeline

    return build_timeline(claims, paper_year_by_id)


class EmptyOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class ToolContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    run_id: str
    phase: AgentPhase
    repository: Any = None


class AgentTool:
    name = "tool"
    description = ""
    input_model: type[BaseModel]
    output_model: type[BaseModel] = EmptyOutput
    allowed_phases: set[AgentPhase] = set()
    artifact_kind: str | None = None

    async def execute(self, context: ToolContext, arguments: BaseModel) -> Any:
        raise NotImplementedError


@dataclass(frozen=True)
class ToolExecution:
    tool_name: str
    arguments: dict[str, Any]
    arguments_hash: str
    attempts: int
    result: ToolResult


class ToolRegistry:
    def __init__(self, tools: Iterable[AgentTool] = ()):
        self._tools: dict[str, AgentTool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def names_for_phase(self, phase: AgentPhase) -> set[str]:
        return {name for name, tool in self._tools.items() if phase in tool.allowed_phases}

    def definitions_for_phase(self, phase: AgentPhase) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_model.model_json_schema(),
            }
            for tool in self._tools.values()
            if phase in tool.allowed_phases
        ]

    @staticmethod
    def arguments_hash(tool_name: str, arguments: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"tool": tool_name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def invoke(
        self,
        tool_name: str,
        raw_arguments: dict[str, Any],
        context: ToolContext,
        *,
        retry_count: int = 0,
    ) -> ToolExecution:
        tool = self.get(tool_name)
        if context.phase not in tool.allowed_phases:
            raise PolicyViolation(f"tool {tool_name!r} is not allowed in {context.phase.value}")

        call_hash = self.arguments_hash(tool_name, raw_arguments)
        try:
            arguments = tool.input_model.model_validate(raw_arguments)
        except ValidationError as exc:
            return ToolExecution(
                tool_name=tool_name,
                arguments=raw_arguments,
                arguments_hash=call_hash,
                attempts=0,
                result=ToolResult(
                    status="failed",
                    error_code="validation_error",
                    error_message=str(exc),
                ),
            )

        attempts = 0
        while attempts <= retry_count:
            attempts += 1
            try:
                raw_result = await tool.execute(context, arguments)
                if isinstance(raw_result, ToolResult):
                    result = raw_result
                else:
                    output = tool.output_model.model_validate(raw_result)
                    result = ToolResult(
                        status="success",
                        data=output.model_dump(mode="json"),
                        summary=f"{tool_name} completed",
                        artifact_kind=tool.artifact_kind,
                    )
                return ToolExecution(
                    tool_name=tool_name,
                    arguments=arguments.model_dump(mode="json"),
                    arguments_hash=call_hash,
                    attempts=attempts,
                    result=result,
                )
            except Exception as exc:
                if attempts <= retry_count:
                    continue
                return ToolExecution(
                    tool_name=tool_name,
                    arguments=arguments.model_dump(mode="json"),
                    arguments_hash=call_hash,
                    attempts=attempts,
                    result=ToolResult(
                        status="failed",
                        error_code="tool_error",
                        error_message=f"{type(exc).__name__}: {exc}",
                    ),
                )

        raise RuntimeError("unreachable")


class PlanResearchInput(BaseModel):
    query: str
    user_context: list[str] = Field(default_factory=list)


class SearchPapersInput(BaseModel):
    query: str
    limit: int = 20
    refresh: bool = False


class EmptyInput(BaseModel):
    pass


class RelationInput(BaseModel):
    refresh: bool = False


class FlexibleOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class PlanResearchTool(AgentTool):
    name = "plan_research"
    description = "Create a focused paper-search plan for the research question."
    input_model = PlanResearchInput
    output_model = FlexibleOutput
    allowed_phases = {AgentPhase.PLAN}
    artifact_kind = "research_plan"

    async def execute(self, context: ToolContext, arguments: PlanResearchInput) -> dict:
        return {
            "question": arguments.query,
            "search_queries": [arguments.query],
            "user_context": arguments.user_context,
        }


class SearchPapersTool(AgentTool):
    name = "search_papers"
    description = "Search scholarly sources and persist normalized papers."
    input_model = SearchPapersInput
    output_model = FlexibleOutput
    allowed_phases = {AgentPhase.DISCOVER}
    artifact_kind = "paper_set"

    async def execute(self, context: ToolContext, arguments: SearchPapersInput) -> dict:
        from sqlalchemy import select
        from database import Paper

        papers_raw, fetched_at = await search_papers_domain(
            arguments.query,
            limit=arguments.limit,
            refresh=arguments.refresh,
        )
        if not papers_raw:
            raise ValueError("no papers with abstracts were found")

        with _require_repository(context).session() as session:
            rows: list[Paper] = []
            for paper in papers_raw:
                row = session.scalar(select(Paper).where(Paper.paper_id == paper["paperId"]))
                if row is None:
                    row = Paper(
                        paper_id=paper["paperId"],
                        title=paper["title"],
                        abstract=paper["abstract"],
                        year=paper.get("year"),
                        authors=", ".join(paper.get("authors") or []),
                        citation_count=int(paper.get("citationCount") or 0),
                    )
                    session.add(row)
                elif arguments.refresh:
                    row.title = paper["title"]
                    row.abstract = paper["abstract"]
                    row.year = paper.get("year")
                    row.authors = ", ".join(paper.get("authors") or [])
                    row.citation_count = int(paper.get("citationCount") or 0)
                rows.append(row)
            session.flush()
            for row in rows:
                session.refresh(row)

            raw_by_id = {paper["paperId"]: paper for paper in papers_raw}
            papers = [
                {
                    "id": row.id,
                    "paper_id": row.paper_id,
                    "title": row.title,
                    "abstract": row.abstract,
                    "year": row.year,
                    "authors": [author for author in row.authors.split(", ") if author],
                    "citation_count": row.citation_count,
                    "doi": raw_by_id.get(row.paper_id, {}).get("doi"),
                    "url": raw_by_id.get(row.paper_id, {}).get("url"),
                    "source": raw_by_id.get(row.paper_id, {}).get("source"),
                }
                for row in rows
            ]
        return {"papers": papers, "data_fetched_at": fetched_at}


class ExtractClaimsTool(AgentTool):
    name = "extract_claims"
    description = "Extract structured claims from the current paper set."
    input_model = EmptyInput
    output_model = FlexibleOutput
    allowed_phases = {AgentPhase.EXTRACT}
    artifact_kind = "claim_set"

    async def execute(self, context: ToolContext, arguments: EmptyInput) -> dict:
        from database import Claim

        artifact = _require_artifact(context, "paper_set")
        papers = artifact.content_json["papers"]
        semaphore = asyncio.Semaphore(4)

        async def extract_one(paper: dict) -> tuple[int, list[dict]]:
            async with semaphore:
                claims = await extract_claims_domain(paper["abstract"])
                return int(paper["id"]), claims

        responses = await asyncio.gather(
            *(extract_one(paper) for paper in papers),
            return_exceptions=True,
        )
        rows: list[Claim] = []
        errors: list[str] = []
        with _require_repository(context).session() as session:
            for response in responses:
                if isinstance(response, BaseException):
                    errors.append(f"{type(response).__name__}: {response}")
                    continue
                paper_id, claims = response
                for claim in claims:
                    row = Claim(
                        paper_id=paper_id,
                        subject=claim["subject"],
                        intervention=claim["intervention"],
                        conclusion=claim["conclusion"],
                        direction=claim["direction"],
                    )
                    session.add(row)
                    rows.append(row)
            session.flush()
            for row in rows:
                session.refresh(row)
            if not rows:
                raise ValueError("no claims could be extracted")
            claims_data = [
                {
                    "id": row.id,
                    "paper_id": row.paper_id,
                    "subject": row.subject,
                    "intervention": row.intervention,
                    "conclusion": row.conclusion,
                    "direction": row.direction,
                }
                for row in rows
            ]
        return {"claims": claims_data, "errors": errors}


class ClassifyRelationsTool(AgentTool):
    name = "classify_relations"
    description = "Classify pairwise support and contradiction relations between claims."
    input_model = RelationInput
    output_model = FlexibleOutput
    allowed_phases = {AgentPhase.ANALYZE}
    artifact_kind = "evidence_graph"

    async def execute(self, context: ToolContext, arguments: RelationInput) -> dict:
        from database import Contradiction

        claims = _require_artifact(context, "claim_set").content_json["claims"]
        papers = _require_artifact(context, "paper_set").content_json["papers"]
        matrix = await build_matrix_domain(
            [
                {
                    "subject": claim["subject"],
                    "intervention": claim["intervention"],
                    "conclusion": claim["conclusion"],
                    "direction": claim["direction"],
                }
                for claim in claims
            ],
            refresh=arguments.refresh,
        )

        with _require_repository(context).session() as session:
            for i in range(len(claims)):
                for j in range(i + 1, len(claims)):
                    cell = matrix[i][j]
                    if cell["relation"] == "unrelated":
                        continue
                    session.add(
                        Contradiction(
                            claim_a_id=claims[i]["id"],
                            claim_b_id=claims[j]["id"],
                            relation=cell["relation"],
                            confidence=cell["confidence"],
                        )
                    )
            session.flush()

        contradict_pairs = _count_relation(matrix, "contradict")
        support_pairs = _count_relation(matrix, "support")
        timeline = build_timeline_domain(
            [
                {
                    "id": claim["id"],
                    "paper_id": claim["paper_id"],
                    "direction": claim["direction"],
                }
                for claim in claims
            ],
            {int(paper["id"]): paper.get("year") for paper in papers},
        )
        return {
            "claims": claims,
            "matrix": matrix,
            "stats": {
                "papers_count": len(papers),
                "claims_count": len(claims),
                "contradict_pairs": contradict_pairs,
                "support_pairs": support_pairs,
            },
            "timeline": timeline,
        }


class GenerateReviewTool(AgentTool):
    name = "generate_review"
    description = "Generate a grounded academic review from the evidence graph."
    input_model = EmptyInput
    output_model = FlexibleOutput
    allowed_phases = {AgentPhase.SYNTHESIZE}
    artifact_kind = "review_draft"

    async def execute(self, context: ToolContext, arguments: EmptyInput) -> dict:
        run = _require_repository(context).get_run(context.run_id)
        evidence = _require_artifact(context, "evidence_graph").content_json
        papers = _require_artifact(context, "paper_set").content_json["papers"]
        return await generate_review_domain(
            claims=evidence["claims"],
            matrix=evidence["matrix"],
            query=run.query,
            papers=papers,
        )


class RecommendDirectionsTool(AgentTool):
    name = "recommend_directions"
    description = "Recommend grounded research questions and methods."
    input_model = EmptyInput
    output_model = FlexibleOutput
    allowed_phases = {AgentPhase.SYNTHESIZE}
    artifact_kind = "recommendations"

    async def execute(self, context: ToolContext, arguments: EmptyInput) -> dict:
        run = _require_repository(context).get_run(context.run_id)
        evidence = _require_artifact(context, "evidence_graph").content_json
        claims = evidence["claims"]
        conflicts = _conflicts_for_prompt(claims, evidence["matrix"])
        prompt = _recommendation_prompt(run.query, claims, conflicts)
        response = await deepseek_chat_domain(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            response_format={"type": "json_object"},
            timeout_s=45.0,
        )
        parsed = json.loads(response["content"])
        if not isinstance(parsed.get("questions"), list) or not isinstance(parsed.get("methods"), list):
            raise ValueError("recommendation response has invalid shape")
        order = {"high": 0, "medium": 1, "low": 2}
        parsed["questions"].sort(key=lambda item: order.get(item.get("priority"), 9))
        parsed["methods"].sort(key=lambda item: order.get(item.get("priority"), 9))
        parsed["meta"] = {
            "elapsed_ms": response.get("elapsed_ms", 0),
            "input_tokens": response.get("input_tokens", 0),
            "output_tokens": response.get("output_tokens", 0),
            "model": response.get("model", ""),
        }
        return parsed


class VerifyEvidenceTool(AgentTool):
    name = "verify_evidence"
    description = "Check citation coverage and evidence graph consistency."
    input_model = EmptyInput
    output_model = FlexibleOutput
    allowed_phases = {AgentPhase.VERIFY}
    artifact_kind = "verification_report"

    async def execute(self, context: ToolContext, arguments: EmptyInput) -> dict:
        papers = _require_artifact(context, "paper_set").content_json["papers"]
        evidence = _require_artifact(context, "evidence_graph").content_json
        review = _require_artifact(context, "review_draft").content_json["review"]
        cited = {int(value) for value in re.findall(r"\[(\d+)\]", review)}
        valid = {number for number in cited if 1 <= number <= len(papers)}
        coverage = len(valid) / len(papers) if papers else 1.0
        matrix = evidence["matrix"]
        matrix_valid = len(matrix) == len(evidence["claims"]) and all(
            len(row) == len(evidence["claims"]) for row in matrix
        )
        return {
            "citation_coverage": round(coverage, 4),
            "cited_papers": sorted(valid),
            "matrix_valid": matrix_valid,
            "grounded": matrix_valid and not (cited - valid),
            "warnings": [] if matrix_valid else ["关系矩阵尺寸与主张数量不一致"],
        }


class FinalizeReportTool(AgentTool):
    name = "finalize_report"
    description = "Assemble the verified final PaperTrace result."
    input_model = EmptyInput
    output_model = FlexibleOutput
    allowed_phases = {AgentPhase.FINALIZE}
    artifact_kind = "final_report"

    async def execute(self, context: ToolContext, arguments: EmptyInput) -> dict:
        repo = _require_repository(context)
        run = repo.get_run(context.run_id)
        paper_set = _require_artifact(context, "paper_set").content_json
        evidence = _require_artifact(context, "evidence_graph").content_json
        review = _require_artifact(context, "review_draft").content_json
        verification = _require_artifact(context, "verification_report").content_json
        recommendations = repo.latest_artifact(context.run_id, "recommendations")
        return {
            "task_id": context.run_id,
            "query": run.query,
            "papers": paper_set["papers"],
            "claims": evidence["claims"],
            "matrix": evidence["matrix"],
            "stats": evidence["stats"],
            "timeline": evidence["timeline"],
            "data_fetched_at": paper_set["data_fetched_at"],
            "review": review["review"],
            "review_meta": {key: value for key, value in review.items() if key != "review"},
            "recommendations": recommendations.content_json if recommendations else None,
            "verification": verification,
        }


def build_default_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            PlanResearchTool(),
            SearchPapersTool(),
            ExtractClaimsTool(),
            ClassifyRelationsTool(),
            GenerateReviewTool(),
            RecommendDirectionsTool(),
            VerifyEvidenceTool(),
            FinalizeReportTool(),
        ]
    )


def _require_repository(context: ToolContext):
    if context.repository is None:
        raise RuntimeError("tool context has no repository")
    return context.repository


def _require_artifact(context: ToolContext, kind: str):
    artifact = _require_repository(context).latest_artifact(context.run_id, kind)
    if artifact is None:
        raise ValueError(f"required artifact is missing: {kind}")
    return artifact


def _count_relation(matrix: list[list[dict]], relation: str) -> int:
    return sum(
        1
        for i in range(len(matrix))
        for j in range(i + 1, len(matrix))
        if matrix[i][j]["relation"] == relation
    )


def _conflicts_for_prompt(claims: list[dict], matrix: list[list[dict]]) -> list[dict]:
    conflicts: list[dict] = []
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            cell = matrix[i][j]
            if cell["relation"] != "contradict" or float(cell["confidence"]) < 0.5:
                continue
            conflicts.append(
                {
                    "id": f"分歧{len(conflicts) + 1}",
                    "topic": claims[i].get("subject") or claims[i].get("intervention") or "",
                    "claimA": claims[i]["conclusion"],
                    "claimB": claims[j]["conclusion"],
                    "confidence": round(float(cell["confidence"]) * 100),
                    "reason": cell.get("reason", ""),
                }
            )
    return conflicts


def _recommendation_prompt(query: str, claims: list[dict], conflicts: list[dict]) -> str:
    compact_claims = [
        {
            "id": f"C{index}",
            "subject": claim["subject"],
            "conclusion": claim["conclusion"],
            "direction": claim["direction"],
        }
        for index, claim in enumerate(claims)
    ]
    return f"""你是学术研究方向推导助手。基于证据和分歧推荐后续研究方向。

研究主题：{query}
矛盾组：{json.dumps(conflicts, ensure_ascii=False)}
核心观点：{json.dumps(compact_claims, ensure_ascii=False)}

只输出 JSON：
{{"questions":[{{"title":"研究问题","desc":"具体价值与切入方式","sources":["分歧1"],"priority":"high"}}],"methods":[{{"title":"方法","desc":"具体方法建议","sources":["分歧1"],"priority":"medium"}}]}}

所有文本使用简体中文；问题必须源自输入证据；sources 只能引用给定分歧 id；questions 生成 3-5 条，methods 生成 2-4 条。"""
