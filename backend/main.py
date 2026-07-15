"""PaperTrace FastAPI entrypoint backed by the persistent Agent Harness."""

from __future__ import annotations

import asyncio
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from agent.repository import AgentRepository
from agent.schemas import AgentPhase, RunStatus
from agent.worker import AgentWorker
from database import init_db

AGENT_REPOSITORY = AgentRepository()
_embedded_worker: AgentWorker | None = None
_embedded_worker_task: asyncio.Task | None = None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _embedded_worker, _embedded_worker_task
    init_db()
    if _env_bool("AGENT_EMBEDDED_WORKER", True):
        _embedded_worker = AgentWorker(AGENT_REPOSITORY)
        _embedded_worker_task = asyncio.create_task(_embedded_worker.serve())
    try:
        yield
    finally:
        if _embedded_worker is not None:
            _embedded_worker.stop()
        if _embedded_worker_task is not None:
            try:
                await asyncio.wait_for(_embedded_worker_task, timeout=3)
            except TimeoutError:
                _embedded_worker_task.cancel()
        _embedded_worker = None
        _embedded_worker_task = None


app = FastAPI(
    title="PaperTrace API",
    description="持久化、可观察、可恢复的学术研究 Agent。",
    version="1.0.0",
    lifespan=_lifespan,
)

_default_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
_extra_origins = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGIN", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins + _extra_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=300)
    limit: int = Field(20, ge=1, le=50)
    refresh: bool = False


class AnalyzeResponse(BaseModel):
    task_id: str
    status: Literal["pending", "running", "done", "failed"]
    message: str


class AgentRunResponse(BaseModel):
    run_id: str
    task_id: str
    status: RunStatus
    current_phase: AgentPhase
    progress: str
    pending_question: str | None
    error_code: str | None
    error_message: str | None
    step_count: int
    token_usage: int
    created_at: str
    updated_at: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: Literal["pending", "running", "done", "failed"]
    progress: str
    error: str | None = None
    created_at: str
    updated_at: str


class PaperOut(BaseModel):
    id: int
    paper_id: str
    title: str
    abstract: str
    year: int | None
    authors: list[str]
    citation_count: int
    doi: str | None = None
    url: str | None = None
    source: str | None = None


class ClaimOut(BaseModel):
    id: int
    paper_id: int
    subject: str
    intervention: str
    conclusion: str
    direction: str


class RelationOut(BaseModel):
    relation: str
    confidence: float
    reason: str


class TimelinePoint(BaseModel):
    year: int
    positive: int
    negative: int
    neutral: int
    total: int


class ResultResponse(BaseModel):
    task_id: str
    query: str
    papers: list[PaperOut]
    claims: list[ClaimOut]
    matrix: list[list[RelationOut]]
    stats: dict[str, Any]
    timeline: list[TimelinePoint]
    data_fetched_at: str
    review: str | None = None
    review_meta: dict[str, Any] | None = None
    recommendations: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None


class ReviewResponse(BaseModel):
    task_id: str
    review: str
    cached: bool = True
    elapsed_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class UserInputRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)


class ExportRequest(BaseModel):
    recommendations: dict[str, Any] | None = None


def _require_run(run_id: str):
    run = AGENT_REPOSITORY.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return run


def _latest_progress(run_id: str, status_value: RunStatus) -> str:
    events = AGENT_REPOSITORY.list_events(run_id)
    if events:
        return events[-1].message or events[-1].event_type
    defaults = {
        RunStatus.QUEUED: "排队中 ...",
        RunStatus.RUNNING: "Agent 正在分析 ...",
        RunStatus.WAITING_INPUT: "等待补充信息",
        RunStatus.COMPLETED: "完成",
        RunStatus.FAILED: "失败",
        RunStatus.CANCELLED: "已取消",
    }
    return defaults[status_value]


def _agent_run_response(run) -> AgentRunResponse:
    return AgentRunResponse(
        run_id=run.id,
        task_id=run.id,
        status=run.status,
        current_phase=run.current_phase,
        progress=_latest_progress(run.id, run.status),
        pending_question=run.pending_question,
        error_code=run.error_code,
        error_message=run.error_message,
        step_count=run.step_count,
        token_usage=run.token_usage,
        created_at=run.created_at.isoformat(),
        updated_at=run.updated_at.isoformat(),
    )


def _legacy_status(run_status: RunStatus) -> str:
    return {
        RunStatus.QUEUED: "pending",
        RunStatus.RUNNING: "running",
        RunStatus.WAITING_INPUT: "running",
        RunStatus.COMPLETED: "done",
        RunStatus.FAILED: "failed",
        RunStatus.CANCELLED: "failed",
    }[run_status]


def _create_run(req: AnalyzeRequest):
    return AGENT_REPOSITORY.create_run(
        query=req.query.strip(),
        paper_limit=req.limit,
        refresh=req.refresh,
    )


def _final_result(run_id: str) -> dict[str, Any]:
    run = _require_run(run_id)
    artifact = AGENT_REPOSITORY.latest_artifact(run_id, "final_report")
    if run.status != RunStatus.COMPLETED or artifact is None:
        raise HTTPException(
            status_code=409,
            detail=f"任务未完成，当前状态：{run.status.value}",
        )
    return artifact.content_json


@app.post("/api/agent/runs", response_model=AgentRunResponse)
async def create_agent_run(req: AnalyzeRequest):
    return _agent_run_response(_create_run(req))


@app.get("/api/agent/runs/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(run_id: str):
    return _agent_run_response(_require_run(run_id))


@app.get("/api/agent/runs/{run_id}/events")
async def get_agent_events(run_id: str, after_seq: int = 0):
    _require_run(run_id)
    events = AGENT_REPOSITORY.list_events(run_id, after_sequence=max(0, after_seq))
    return {
        "run_id": run_id,
        "events": [
            {
                "sequence": event.sequence,
                "event_type": event.event_type,
                "phase": event.phase.value if event.phase else None,
                "message": event.message,
                "payload": event.payload_json,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ],
    }


@app.get("/api/agent/runs/{run_id}/trace")
async def get_agent_trace(run_id: str):
    _require_run(run_id)
    steps = AGENT_REPOSITORY.list_steps(run_id)
    calls = AGENT_REPOSITORY.list_tool_calls(run_id)
    return {
        "run_id": run_id,
        "steps": [
            {
                "id": step.id,
                "sequence": step.sequence,
                "phase": step.phase.value,
                "action_type": step.action_type,
                "action_summary": step.action_summary,
                "status": step.status,
                "model_name": step.model_name,
                "input_tokens": step.input_tokens,
                "output_tokens": step.output_tokens,
                "error_code": step.error_code,
                "error_message": step.error_message,
                "started_at": step.started_at.isoformat(),
                "finished_at": step.finished_at.isoformat() if step.finished_at else None,
            }
            for step in steps
        ],
        "tool_calls": [
            {
                "id": call.id,
                "step_id": call.step_id,
                "tool_name": call.tool_name,
                "arguments": call.arguments_json,
                "status": call.status,
                "result_summary": call.result_summary,
                "artifact_ids": call.artifact_ids_json,
                "duration_ms": call.duration_ms,
                "retry_count": call.retry_count,
                "error_code": call.error_code,
                "error_message": call.error_message,
            }
            for call in calls
        ],
    }


@app.post("/api/agent/runs/{run_id}/input", response_model=AgentRunResponse)
async def submit_agent_input(run_id: str, body: UserInputRequest):
    _require_run(run_id)
    try:
        run = AGENT_REPOSITORY.submit_input(run_id, body.content.strip())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    AGENT_REPOSITORY.append_event(run_id, "run.input_received", "已收到补充信息")
    return _agent_run_response(run)


@app.post("/api/agent/runs/{run_id}/cancel", response_model=AgentRunResponse)
async def cancel_agent_run(run_id: str):
    run = _require_run(run_id)
    try:
        run = AGENT_REPOSITORY.transition(run.id, RunStatus.CANCELLED)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    AGENT_REPOSITORY.append_event(run_id, "run.cancelled", "任务已取消")
    return _agent_run_response(run)


@app.post("/api/agent/runs/{run_id}/retry", response_model=AgentRunResponse)
async def retry_agent_run(run_id: str):
    _require_run(run_id)
    try:
        run = AGENT_REPOSITORY.retry_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    AGENT_REPOSITORY.append_event(run_id, "run.retried", "任务重新排队")
    return _agent_run_response(run)


@app.get("/api/agent/runs/{run_id}/result", response_model=ResultResponse)
async def get_agent_result(run_id: str):
    return _final_result(run_id)


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    run = _create_run(req)
    return AnalyzeResponse(
        task_id=run.id,
        status="pending",
        message="任务已提交，请用 /api/task/{task_id} 查询进度",
    )


@app.get("/api/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    run = _require_run(task_id)
    return TaskStatusResponse(
        task_id=run.id,
        status=_legacy_status(run.status),
        progress=_latest_progress(run.id, run.status),
        error=run.error_message,
        created_at=run.created_at.isoformat(),
        updated_at=run.updated_at.isoformat(),
    )


@app.get("/api/result/{task_id}", response_model=ResultResponse)
async def get_task_result(task_id: str):
    return _final_result(task_id)


@app.get("/api/review/{task_id}", response_model=ReviewResponse)
async def get_review(task_id: str):
    result = _final_result(task_id)
    meta = result.get("review_meta") or {}
    return ReviewResponse(
        task_id=task_id,
        review=result.get("review") or "",
        cached=True,
        elapsed_ms=int(meta.get("elapsed_ms", 0)),
        input_tokens=int(meta.get("input_tokens", 0)),
        output_tokens=int(meta.get("output_tokens", 0)),
        model=str(meta.get("model", "")),
    )


def _build_markdown_report(result: dict[str, Any]) -> str:
    papers = result.get("papers") or []
    claims = result.get("claims") or []
    matrix = result.get("matrix") or []
    stats = result.get("stats") or {}
    recommendations = result.get("recommendations") or {}
    lines = [
        "# PaperTrace 分析报告",
        "",
        f"**研究问题**：{result.get('query', '')}  ",
        f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"**数据获取于**：{result.get('data_fetched_at', '')}  ",
        f"**任务 ID**：`{result.get('task_id', '')}`",
        "",
        (
            f"> 共检索 {stats.get('papers_count', len(papers))} 篇论文，"
            f"抽取 {stats.get('claims_count', len(claims))} 条主张，"
            f"发现 {stats.get('contradict_pairs', 0)} 对矛盾。"
        ),
        "",
        "## 一、AI 综述",
        "",
        result.get("review") or "尚未生成综述。",
        "",
        "## 二、核心矛盾",
        "",
    ]
    contradiction_count = 0
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            cell = matrix[i][j]
            if cell.get("relation") != "contradict":
                continue
            contradiction_count += 1
            lines.extend(
                [
                    f"### 矛盾 {contradiction_count}",
                    "",
                    f"- 观点 A：{claims[i]['conclusion']}",
                    f"- 观点 B：{claims[j]['conclusion']}",
                    f"- 置信度：{float(cell.get('confidence', 0)):.0%}",
                    f"- 判定依据：{cell.get('reason', '')}",
                    "",
                ]
            )
    if contradiction_count == 0:
        lines.extend(["未识别到高置信度矛盾。", ""])

    lines.extend(["## 三、研究方向", ""])
    questions = recommendations.get("questions") or []
    methods = recommendations.get("methods") or []
    if not questions and not methods:
        lines.extend(["尚未生成研究方向。", ""])
    for item in questions:
        lines.extend([f"- **{item.get('title', '')}**：{item.get('desc', '')}"])
    for item in methods:
        lines.extend([f"- **{item.get('title', '')}**：{item.get('desc', '')}"])

    lines.extend(["", "## 四、论文与主张", ""])
    claims_by_paper: dict[int, list[dict]] = {}
    for claim in claims:
        claims_by_paper.setdefault(int(claim["paper_id"]), []).append(claim)
    for index, paper in enumerate(papers, 1):
        lines.extend(
            [
                f"### [{index}] {paper['title']}",
                "",
                f"- 年份：{paper.get('year') or '未知'}",
                f"- 作者：{', '.join(paper.get('authors') or []) or '未知'}",
                f"- 引用数：{paper.get('citation_count', 0)}",
            ]
        )
        if paper.get("url"):
            lines.append(f"- 链接：{paper['url']}")
        for claim in claims_by_paper.get(int(paper["id"]), []):
            lines.append(
                f"- 主张（{claim['direction']}）：{claim['conclusion']}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _bibtex_escape(value: str) -> str:
    return value.replace("\\", "\\textbackslash{}").replace("{", "\\{").replace("}", "\\}")


def _build_bibtex(papers: list[dict[str, Any]]) -> str:
    entries: list[str] = []
    used: set[str] = set()
    for index, paper in enumerate(papers, 1):
        authors = paper.get("authors") or []
        surname = re.sub(r"[^A-Za-z0-9]", "", (authors[0].split()[-1] if authors else "Paper"))
        base = f"{surname}{paper.get('year') or 'nd'}"
        key = base
        suffix = 2
        while key in used:
            key = f"{base}{suffix}"
            suffix += 1
        used.add(key)
        fields = [
            f"  title = {{{_bibtex_escape(str(paper.get('title') or 'Untitled'))}}}",
            f"  author = {{{' and '.join(authors) or 'Unknown'}}}",
            f"  year = {{{paper.get('year') or ''}}}",
        ]
        if paper.get("doi"):
            fields.append(f"  doi = {{{paper['doi']}}}")
        if paper.get("url"):
            fields.append(f"  url = {{{paper['url']}}}")
        entries.append(f"@article{{{key},\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(entries) + ("\n" if entries else "")


@app.post("/api/export/{task_id}")
@app.post("/api/agent/runs/{task_id}/export")
async def export_report(
    task_id: str,
    format: str = "md",
    body: ExportRequest | None = None,
):
    if format not in {"md", "bib"}:
        raise HTTPException(status_code=400, detail=f"暂不支持的导出格式：{format}")
    result = dict(_final_result(task_id))
    if body and body.recommendations is not None:
        result["recommendations"] = body.recommendations

    safe_query = re.sub(r"[^\w-]", "_", result.get("query") or "report")[:40]
    date_value = datetime.now().strftime("%Y%m%d")
    if format == "md":
        content = _build_markdown_report(result).encode("utf-8")
        media_type = "text/markdown; charset=utf-8"
        filename = f"PaperTrace_报告_{safe_query}_{date_value}.md"
    else:
        content = _build_bibtex(result.get("papers") or []).encode("utf-8")
        media_type = "application/x-bibtex; charset=utf-8"
        filename = f"PaperTrace_文献_{safe_query}_{date_value}.bib"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/")
async def root():
    return {
        "service": "PaperTrace API",
        "status": "ok",
        "task_store": "database",
        "embedded_worker": _env_bool("AGENT_EMBEDDED_WORKER", True),
    }
