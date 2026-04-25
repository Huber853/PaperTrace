"""
PaperTrace - 切片 6：FastAPI 后端入口
=======================================

作用：把前面五个切片的能力（fetcher / database / extractor / contradiction）
      串成 HTTP API，供切片 7 的 Next.js 前端调用。

提供 3 个接口：
  1. POST /api/analyze         提交一次分析任务，立刻返回 task_id
  2. GET  /api/task/{task_id}  查询任务状态
  3. GET  /api/result/{task_id} 取完整结果（论文 + 主张 + 矩阵）

----------------------------------------------------
新手必读：什么是 BackgroundTasks ？
----------------------------------------------------
分析一次要做四件事：拉论文 → 抽主张 → 两两判定 → 入库。
全跑完可能要 30 秒到 2 分钟。
如果让用户在浏览器里干等这么久，很可能：
  - 浏览器超时断开
  - 用户以为页面卡了刷新页面
  - 反向代理（Nginx/CDN）会强制 502

正确做法：
  - POST /api/analyze 立刻返回一个 task_id（瞬间响应）
  - 真正的活儿放到"后台任务"里跑
  - 前端拿到 task_id 后定时轮询 /api/task/{task_id}，看好了再去取结果

FastAPI 的 BackgroundTasks 是最简单的实现方式：
  - 你在路由函数里写 background_tasks.add_task(my_func, arg1, arg2)
  - FastAPI 会在 HTTP 响应发出去之后，在同一个 Python 进程里跑这个函数
  - 适合"几十秒到几分钟"的活儿，不适合"几小时"的活儿（那种要用 Celery / RQ）

----------------------------------------------------
为什么任务状态用内存 dict 而不是数据库表？
----------------------------------------------------
- 比赛/MVP 阶段，进程不会重启，内存 dict 完全够用，不用建第 4 张表
- 生产环境想做持久化，未来加一张 tasks 表即可，对前端零改动
- 这种"先 MVP 后扩展"的取舍是工程实践
"""

# ===== 导入区 =====
from __future__ import annotations

import asyncio                                # 异步并发
import os                                     # 读环境变量（部署时配 CORS / PORT）
import traceback                              # 后台任务出错时打全栈
import uuid                                   # 生成 task_id
from datetime import datetime, timezone
from typing import Literal

import httpx                                   # 用于捕获 /api/chat 代理的超时 / 状态码错误
from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from urllib.parse import quote
from pydantic import BaseModel, Field
from sqlalchemy import select

# 导入我们前 5 个切片写好的模块
from fetcher import search_papers
from database import (
    Base, engine, get_session,
    Paper, Claim, Contradiction,
    init_db,
)
from extractor import extract_claims
from contradiction import build_matrix
from generator import deepseek_chat, generate_review
from timeline import build_timeline


# ===== 创建 FastAPI 应用 =====
app = FastAPI(
    title="PaperTrace API",
    description="学术论文矛盾发现工具——从研究问题到矛盾矩阵和综述。",
    version="0.1.0",
)


# ===== CORS 配置 =====
# 前端在 localhost:3000 跑，后端在 localhost:8000 跑，跨域必须放行。
#
# 部署时怎么改？
#   - Render 的环境变量里加一个 FRONTEND_ORIGIN=https://papertrace-xxx.vercel.app
#   - 多个域名用英文逗号分隔，比如：
#       FRONTEND_ORIGIN=https://papertrace.vercel.app,https://staging-papertrace.vercel.app
#   - 不配的话只允许本地三个开发地址
#
# 安全提示：千万不要图省事写 allow_origins=["*"]，那样别人的网页也能打你的接口
_default_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
_extra_origins = [
    o.strip()
    for o in os.getenv("FRONTEND_ORIGIN", "").split(",")
    if o.strip()  # 跳过空字符串
]
ALLOWED_ORIGINS = _default_origins + _extra_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],   # GET / POST / OPTIONS 全放行
    allow_headers=["*"],
)


# ===== 启动事件：建库 =====
# 用 lifespan 比 on_event 更现代，但 on_event 更直观
@app.on_event("startup")
def _on_startup():
    init_db()


# ===== Pydantic 请求/响应模型 =====
class AnalyzeRequest(BaseModel):
    """POST /api/analyze 的请求体。"""
    query: str = Field(..., min_length=1, max_length=300, description="研究问题关键词")
    limit: int = Field(20, ge=1, le=50, description="拉多少篇论文，1~50")
    refresh: bool = Field(
        False,
        description="True 时跳过本地缓存，强制重新调外部数据源。前端的"
                    "'刷新数据'按钮会带这个参数。",
    )


class AnalyzeResponse(BaseModel):
    """POST /api/analyze 的响应。"""
    task_id: str
    status: Literal["pending", "running", "done", "failed"]
    message: str


class TaskStatusResponse(BaseModel):
    """GET /api/task/{task_id} 的响应。"""
    task_id: str
    status: Literal["pending", "running", "done", "failed"]
    progress: str            # 人类可读的进度描述（如"正在抽取主张 5/12"）
    error: str | None = None
    created_at: str
    updated_at: str


class PaperOut(BaseModel):
    """返回给前端的一篇论文。"""
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
    """返回给前端的一条主张。"""
    id: int
    paper_id: int
    subject: str
    intervention: str
    conclusion: str
    direction: str


class RelationOut(BaseModel):
    """矩阵中的一个格子。"""
    relation: str
    confidence: float
    reason: str


class TimelinePoint(BaseModel):
    """时间轴上的一个年份数据点。"""
    year: int
    positive: int
    negative: int
    neutral: int
    total: int


class ResultResponse(BaseModel):
    """GET /api/result/{task_id} 的响应。"""
    task_id: str
    query: str
    papers: list[PaperOut]
    claims: list[ClaimOut]
    matrix: list[list[RelationOut]]   # claims 顺序对齐
    stats: dict
    timeline: list[TimelinePoint]     # 观点演化时间轴
    data_fetched_at: str  # ISO 8601 时间戳；这批论文最初是何时从源拉到的


class ReviewResponse(BaseModel):
    """GET /api/review/{task_id} 的响应。"""
    task_id: str
    review: str
    cached: bool  # True 表示这次是从缓存取的
    elapsed_ms: int = 0        # LLM 生成耗时；缓存命中时是首次生成时记录的值
    input_tokens: int = 0      # prompt token 数
    output_tokens: int = 0     # completion token 数
    model: str = ""            # 实际使用的模型名


# ===== /api/chat 通用 DeepSeek 代理 =====
# 前端要用 response_format=json_object 让模型直接吐结构化数据时,
# 走这个通道而不是自己写一套业务路由。不用于对外开放, 仅供本站前端调用。
class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=20)
    temperature: float = Field(0.5, ge=0.0, le=2.0)
    response_format: dict | None = None  # 如 {"type": "json_object"}


class ChatResponse(BaseModel):
    content: str
    elapsed_ms: int
    input_tokens: int
    output_tokens: int
    model: str


# ===== 任务存储（进程内内存）=====
# 结构：
# {
#   "task_id_xxx": {
#       "status": "pending"|"running"|"done"|"failed",
#       "progress": "...",
#       "error": None|str,
#       "query": "...",
#       "result": None|ResultResponse-shaped dict,
#       "created_at": iso str,
#       "updated_at": iso str,
#   }
# }
TASKS: dict[str, dict] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_task(task_id: str, **fields):
    """统一更新任务状态，并刷新 updated_at。"""
    if task_id not in TASKS:
        return
    TASKS[task_id].update(fields)
    TASKS[task_id]["updated_at"] = _now_iso()


# ===== 后台任务：完整分析流水线 =====
async def _run_analysis(task_id: str, query: str, limit: int, refresh: bool = False):
    """
    后台任务函数。
    被 BackgroundTasks 调用，在 HTTP 响应发出去之后才开始执行。
    任何一步出错都会被捕获并更新任务状态。

    refresh=True 时会把 refresh 透传给 search_papers，跳过本地缓存。
    """
    try:
        _update_task(task_id, status="running", progress="正在搜索论文 ...")

        # === 第 1 步：拉论文 ===
        # search_papers 现在返回 (papers, fetched_at) 元组
        # fetched_at 用于前端展示 "数据获取于 XX 时间"
        papers_raw, data_fetched_at = await search_papers(
            query, limit=limit, refresh=refresh
        )
        if not papers_raw:
            _update_task(
                task_id,
                status="failed",
                error="未搜索到任何带摘要的论文，换个关键词试试",
            )
            return
        _update_task(task_id, progress=f"搜到 {len(papers_raw)} 篇论文，正在入库 ...")

        # === 第 2 步：写入 papers 表 ===
        # 注意：同一个 query 多次跑可能拉到重复论文，
        # 我们用 paper_id 作为去重 key，已存在的就复用
        session = get_session()
        try:
            db_papers: list[Paper] = []
            for p in papers_raw:
                existing = session.scalar(
                    select(Paper).where(Paper.paper_id == p["paperId"])
                )
                if existing:
                    if refresh:
                        existing.title = p["title"]
                        existing.abstract = p["abstract"]
                        existing.year = p["year"]
                        existing.authors = ", ".join(p["authors"])
                        existing.citation_count = p["citationCount"]
                    db_papers.append(existing)
                    continue
                row = Paper(
                    paper_id=p["paperId"],
                    title=p["title"],
                    abstract=p["abstract"],
                    year=p["year"],
                    authors=", ".join(p["authors"]),
                    citation_count=p["citationCount"],
                )
                session.add(row)
                db_papers.append(row)
            session.commit()
            # commit 后拿到自增 id；后面要按 id 关联 claims
            for row in db_papers:
                session.refresh(row)
        finally:
            session.close()

        # === 第 3 步：对每篇论文抽主张（并发，但限速避免限流）===
        _update_task(task_id, progress=f"正在从 {len(db_papers)} 篇论文中抽取主张 ...")

        sem = asyncio.Semaphore(4)  # 最多 4 篇同时调 DeepSeek

        async def _extract_one(paper_row: Paper) -> tuple[int, list[dict]]:
            async with sem:
                claims = await extract_claims(paper_row.abstract)
                return paper_row.id, claims

        extract_tasks = [_extract_one(p) for p in db_papers]
        extract_results = await asyncio.gather(*extract_tasks, return_exceptions=True)

        # === 第 4 步：把主张写入 claims 表 ===
        session = get_session()
        all_claim_rows: list[Claim] = []
        try:
            for res in extract_results:
                if isinstance(res, Exception):
                    # 单篇抽取失败不影响整体，跳过
                    print(f"[task] 抽取异常：{res}")
                    continue
                paper_db_id, claims = res
                for c in claims:
                    row = Claim(
                        paper_id=paper_db_id,
                        subject=c["subject"],
                        intervention=c["intervention"],
                        conclusion=c["conclusion"],
                        direction=c["direction"],
                    )
                    session.add(row)
                    all_claim_rows.append(row)
            session.commit()
            for row in all_claim_rows:
                session.refresh(row)
        finally:
            session.close()

        if not all_claim_rows:
            _update_task(
                task_id,
                status="failed",
                error="所有论文都没抽到任何主张，可能 DeepSeek 调用失败",
            )
            return

        _update_task(
            task_id,
            progress=f"抽到 {len(all_claim_rows)} 条主张，正在两两判定关系 ...",
        )

        # === 第 5 步：构建矩阵 ===
        # build_matrix 需要 dict 形式的 claims，把 ORM 对象转回 dict
        claims_for_matrix = [
            {
                "subject": c.subject,
                "intervention": c.intervention,
                "conclusion": c.conclusion,
                "direction": c.direction,
            }
            for c in all_claim_rows
        ]
        matrix = await build_matrix(claims_for_matrix, refresh=refresh)

        # === 第 6 步：把非平凡的关系写入 contradictions 表（只存 i<j 的上三角）===
        session = get_session()
        try:
            for i in range(len(all_claim_rows)):
                for j in range(i + 1, len(all_claim_rows)):
                    cell = matrix[i][j]
                    # 只入库 contradict 和 support，unrelated 不存（数据量太大）
                    if cell["relation"] == "unrelated":
                        continue
                    rel = Contradiction(
                        claim_a_id=all_claim_rows[i].id,
                        claim_b_id=all_claim_rows[j].id,
                        relation=cell["relation"],
                        confidence=cell["confidence"],
                    )
                    session.add(rel)
            session.commit()
        finally:
            session.close()

        # === 第 7 步：组装结果，存入 TASKS ===
        # papers_raw 有 doi/url/source 等额外字段，DB 里没存，需要合并回来
        raw_by_id = {p["paperId"]: p for p in papers_raw}
        result = {
            "task_id": task_id,
            "query": query,
            "papers": [
                {
                    "id": p.id,
                    "paper_id": p.paper_id,
                    "title": p.title,
                    "abstract": p.abstract,
                    "year": p.year,
                    "authors": [a for a in p.authors.split(", ") if a],
                    "citation_count": p.citation_count,
                    "doi": raw_by_id.get(p.paper_id, {}).get("doi"),
                    "url": raw_by_id.get(p.paper_id, {}).get("url"),
                    "source": raw_by_id.get(p.paper_id, {}).get("source"),
                }
                for p in db_papers
            ],
            "claims": [
                {
                    "id": c.id,
                    "paper_id": c.paper_id,
                    "subject": c.subject,
                    "intervention": c.intervention,
                    "conclusion": c.conclusion,
                    "direction": c.direction,
                }
                for c in all_claim_rows
            ],
            "matrix": matrix,
            "stats": {
                "papers_count": len(db_papers),
                "claims_count": len(all_claim_rows),
                "contradict_pairs": sum(
                    1 for i in range(len(all_claim_rows))
                    for j in range(i + 1, len(all_claim_rows))
                    if matrix[i][j]["relation"] == "contradict"
                ),
                "support_pairs": sum(
                    1 for i in range(len(all_claim_rows))
                    for j in range(i + 1, len(all_claim_rows))
                    if matrix[i][j]["relation"] == "support"
                ),
            },
            # 观点演化时间轴：把每条 claim 按论文年份聚合
            "timeline": build_timeline(
                [
                    {"id": c.id, "paper_id": c.paper_id, "direction": c.direction}
                    for c in all_claim_rows
                ],
                {p.id: p.year for p in db_papers},
            ),
            # 从 search_papers 透传上来的"数据原始获取时间"
            # 缓存命中时是当初写缓存的时间，未命中或 refresh=True 时是"刚刚"
            "data_fetched_at": data_fetched_at,
        }

        _update_task(
            task_id,
            status="done",
            progress="完成",
            result=result,
        )

    except Exception as e:
        # 最外层兜底：任何异常都标记为 failed
        traceback.print_exc()
        _update_task(
            task_id,
            status="failed",
            error=f"{type(e).__name__}: {e}",
        )


# ===== 接口 1：POST /api/analyze =====
@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    """
    提交一次分析任务。立刻返回 task_id，真正的活儿放后台跑。

    流程：拉论文 → 入库 → 抽主张 → 构建矩阵 → 写库
    """
    task_id = uuid.uuid4().hex
    TASKS[task_id] = {
        "status": "pending",
        "progress": "排队中 ...",
        "error": None,
        "query": req.query,
        "result": None,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    # add_task 把函数加入"响应发出后再执行"的队列
    # 注意：传函数本身和参数，FastAPI 自己会 await 它
    background_tasks.add_task(
        _run_analysis, task_id, req.query, req.limit, req.refresh
    )

    return AnalyzeResponse(
        task_id=task_id,
        status="pending",
        message=(
            "任务已提交（强制刷新），请用 /api/task/{task_id} 查询进度"
            if req.refresh
            else "任务已提交，请用 /api/task/{task_id} 查询进度"
        ),
    )


# ===== 接口 2：GET /api/task/{task_id} =====
@app.get("/api/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """轮询任务状态。前端每 2 秒打一次。"""
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    return TaskStatusResponse(
        task_id=task_id,
        status=task["status"],
        progress=task["progress"],
        error=task["error"],
        created_at=task["created_at"],
        updated_at=task["updated_at"],
    )


# ===== 接口 3：GET /api/result/{task_id} =====
@app.get("/api/result/{task_id}", response_model=ResultResponse)
async def get_task_result(task_id: str):
    """获取一个已完成任务的完整结果。"""
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] != "done":
        raise HTTPException(
            status_code=409,
            detail=f"任务未完成，当前状态：{task['status']}",
        )
    return task["result"]


# ===== 接口 4：GET /api/review/{task_id} =====
@app.get("/api/review/{task_id}", response_model=ReviewResponse)
async def get_review(task_id: str):
    """
    基于已完成任务的结果，生成一段 200-400 字的学术综述。
    首次调用会真的跑一次 DeepSeek；之后的调用直接返回缓存。
    """
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] != "done":
        raise HTTPException(
            status_code=409,
            detail=f"任务未完成，当前状态：{task['status']}",
        )

    # 缓存命中：直接返回（连带把首次生成时记录的 token/耗时一起返回）
    if task.get("review"):
        meta = task.get("review_meta") or {}
        return ReviewResponse(
            task_id=task_id,
            review=task["review"],
            cached=True,
            elapsed_ms=int(meta.get("elapsed_ms", 0)),
            input_tokens=int(meta.get("input_tokens", 0)),
            output_tokens=int(meta.get("output_tokens", 0)),
            model=str(meta.get("model", "")),
        )

    # 首次生成：调 generator
    result = task["result"]
    try:
        gen = await generate_review(
            claims=result["claims"],
            matrix=result["matrix"],
            query=result["query"],
            papers=result["papers"],
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"生成综述失败: {type(e).__name__}: {e}",
        )

    # 存缓存（文本 + 元数据）
    task["review"] = gen["review"]
    task["review_meta"] = {
        "elapsed_ms": gen["elapsed_ms"],
        "input_tokens": gen["input_tokens"],
        "output_tokens": gen["output_tokens"],
        "model": gen["model"],
    }
    task["updated_at"] = _now_iso()

    return ReviewResponse(
        task_id=task_id,
        review=gen["review"],
        cached=False,
        elapsed_ms=gen["elapsed_ms"],
        input_tokens=gen["input_tokens"],
        output_tokens=gen["output_tokens"],
        model=gen["model"],
    )


# ===== 接口 4.5：POST /api/chat =====
@app.post("/api/chat", response_model=ChatResponse)
async def chat_completion(req: ChatRequest):
    """
    通用 DeepSeek 代理。前端传 messages + temperature + response_format,
    后端用已配置好的 API key 透传给 DeepSeek, 返回 content + token 统计。

    注意: 这是个原始通道, 不做业务缓存、不做 prompt 模板、不做权限检查。
          仅供本站前端"研究方向建议"这类轻量功能使用。
    """
    try:
        result = await deepseek_chat(
            messages=[m.model_dump() for m in req.messages],
            temperature=req.temperature,
            response_format=req.response_format,
            timeout_s=45.0,
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="DeepSeek 请求超时")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"DeepSeek {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    return ChatResponse(
        content=result["content"],
        elapsed_ms=result["elapsed_ms"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        model=result["model"],
    )


# ===== 接口 5：GET /api/export/{task_id} =====
def _build_markdown_report(task_id: str, result: dict, review_text: str | None) -> str:
    query = result.get("query", "")
    papers = result.get("papers", []) or []
    claims = result.get("claims", []) or []
    matrix = result.get("matrix", []) or []
    stats = result.get("stats", {}) or {}
    fetched_at = result.get("data_fetched_at", "")

    paper_by_id = {p["id"]: p for p in papers}
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []
    lines.append(f"# PaperTrace 分析报告")
    lines.append("")
    lines.append(f"**研究问题**：{query}  ")
    lines.append(f"**生成时间**：{now_str}  ")
    if fetched_at:
        lines.append(f"**数据获取于**：{fetched_at}  ")
    lines.append(f"**任务 ID**：`{task_id}`")
    lines.append("")
    lines.append(
        f"> 共检索 {stats.get('papers_count', len(papers))} 篇论文，"
        f"抽取 {stats.get('claims_count', len(claims))} 条核心主张，"
        f"发现 {stats.get('contradict_pairs', 0)} 对矛盾、"
        f"{stats.get('support_pairs', 0)} 对支持关系。"
    )
    lines.append("")

    lines.append("## 一、检索到的论文列表")
    lines.append("")
    if not papers:
        lines.append("_（无）_")
    else:
        for i, p in enumerate(papers, 1):
            authors = ", ".join((p.get("authors") or [])[:5])
            if len(p.get("authors") or []) > 5:
                authors += " 等"
            year = p.get("year") or "—"
            doi = p.get("doi")
            url = p.get("url")
            link = None
            if doi:
                link = f"https://doi.org/{doi}"
            elif url:
                link = url
            title = p.get("title", "")
            title_line = f"**[{title}]({link})**" if link else f"**{title}**"
            lines.append(f"{i}. {title_line}")
            lines.append(f"   - 作者：{authors or '—'}")
            lines.append(f"   - 年份：{year}")
            if doi:
                lines.append(f"   - DOI：`{doi}`")
            if p.get("citation_count"):
                lines.append(f"   - 引用数：{p['citation_count']}")
    lines.append("")

    lines.append("## 二、核心主张列表")
    lines.append("")
    if not claims:
        lines.append("_（无）_")
    else:
        dir_label = {"positive": "支持", "negative": "反对", "neutral": "中立"}
        for i, c in enumerate(claims, 1):
            paper = paper_by_id.get(c.get("paper_id"))
            paper_title = paper.get("title", "") if paper else ""
            direction = dir_label.get(c.get("direction", ""), c.get("direction", ""))
            lines.append(f"### 主张 #{i} · {direction}")
            if paper_title:
                lines.append(f"来源论文：_{paper_title}_")
            lines.append("")
            lines.append(f"- **主题**：{c.get('subject', '')}")
            lines.append(f"- **干预**：{c.get('intervention', '')}")
            lines.append(f"- **结论**：{c.get('conclusion', '')}")
            lines.append("")

    lines.append("## 三、矛盾关系摘要")
    lines.append("")
    contradictions: list[tuple[int, int, dict]] = []
    n = len(claims)
    for i in range(n):
        for j in range(i + 1, n):
            try:
                cell = matrix[i][j]
            except (IndexError, TypeError):
                continue
            if not cell:
                continue
            rel = cell.get("relation") if isinstance(cell, dict) else getattr(cell, "relation", None)
            if rel == "contradict":
                cell_d = cell if isinstance(cell, dict) else cell.model_dump()
                contradictions.append((i, j, cell_d))
    if not contradictions:
        lines.append("_未发现明显的矛盾关系。_")
    else:
        lines.append(f"共发现 **{len(contradictions)}** 对矛盾：")
        lines.append("")
        for i, j, cell in contradictions:
            ci = claims[i]
            cj = claims[j]
            pi = paper_by_id.get(ci.get("paper_id"), {})
            pj = paper_by_id.get(cj.get("paper_id"), {})
            conf = cell.get("confidence", 0)
            reason = cell.get("reason", "")
            lines.append(f"- **主张 #{i+1}** vs **主张 #{j+1}**（置信度 {conf*100:.0f}%）")
            lines.append(f"  - #{i+1}：{ci.get('conclusion','')}  _（{pi.get('title','')}）_")
            lines.append(f"  - #{j+1}：{cj.get('conclusion','')}  _（{pj.get('title','')}）_")
            if reason:
                lines.append(f"  - AI 判定理由：{reason}")
    lines.append("")

    lines.append("## 四、AI 综述")
    lines.append("")
    if review_text:
        lines.append(review_text)
    else:
        lines.append("_该任务尚未生成综述（请在结果页点击“一键生成综述”后再导出）。_")
    lines.append("")

    lines.append("---")
    lines.append("*本报告由 PaperTrace 自动生成。*")
    return "\n".join(lines)


def _bibtex_escape(s: str) -> str:
    """BibTeX 字段值里的 { } \\ 需要转义。"""
    if not s:
        return ""
    return (
        s.replace("\\", "\\textbackslash{}")
         .replace("{", "\\{")
         .replace("}", "\\}")
         .replace("&", "\\&")
         .replace("%", "\\%")
         .replace("$", "\\$")
         .replace("#", "\\#")
         .replace("_", "\\_")
    )


def _bib_key(paper: dict, index: int) -> str:
    """构造一个尽量稳定的 BibTeX key：firstAuthorLastName + year + index。"""
    authors = paper.get("authors") or []
    first = authors[0] if authors else "anon"
    last = first.strip().split()[-1] if first else "anon"
    last = "".join(ch for ch in last if ch.isalnum()) or "anon"
    year = paper.get("year") or "nd"
    return f"{last}{year}_{index}"


def _build_bibtex(papers: list[dict]) -> str:
    """把论文列表转成 .bib 文本。"""
    blocks: list[str] = []
    for idx, p in enumerate(papers, 1):
        key = _bib_key(p, idx)
        authors = " and ".join(p.get("authors") or [])
        title = p.get("title") or ""
        year = p.get("year")
        doi = p.get("doi")
        url = p.get("url")
        source = p.get("source") or "misc"
        # 大多数情况没有 journal 字段 → 用 @article/@misc 兜底
        kind = "article" if year else "misc"
        fields: list[str] = []
        if title:
            fields.append(f"  title = {{{_bibtex_escape(title)}}}")
        if authors:
            fields.append(f"  author = {{{_bibtex_escape(authors)}}}")
        if year:
            fields.append(f"  year = {{{year}}}")
        if doi:
            fields.append(f"  doi = {{{_bibtex_escape(doi)}}}")
        if url:
            fields.append(f"  url = {{{_bibtex_escape(url)}}}")
        fields.append(f"  note = {{Source: {_bibtex_escape(source)}}}")
        blocks.append("@" + kind + "{" + key + ",\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(blocks) + "\n"


@app.get("/api/export/{task_id}")
async def export_report(task_id: str, format: str = "md"):
    """导出分析报告。支持 format=md | bib。"""
    if format not in ("md", "bib"):
        raise HTTPException(status_code=400, detail=f"暂不支持的导出格式：{format}")
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] != "done":
        raise HTTPException(status_code=409, detail=f"任务未完成，当前状态：{task['status']}")

    result = task["result"]

    query = (result.get("query") or "report").strip().replace(" ", "_")
    safe_query = "".join(ch for ch in query if ch.isalnum() or ch in "_-") or "report"
    if len(safe_query) > 40:
        safe_query = safe_query[:40]
    date_str = datetime.now().strftime("%Y%m%d")

    if format == "md":
        body = _build_markdown_report(task_id, result, task.get("review")).encode("utf-8")
        media = "text/markdown; charset=utf-8"
        filename = f"PaperTrace_报告_{safe_query}_{date_str}.md"
    else:  # bib
        body = _build_bibtex(result.get("papers") or []).encode("utf-8")
        media = "application/x-bibtex; charset=utf-8"
        filename = f"PaperTrace_文献_{safe_query}_{date_str}.bib"

    return Response(
        content=body,
        media_type=media,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


# ===== 健康检查 / 根路径 =====
@app.get("/")
async def root():
    """活体探针，部署到 Render 后用来确认服务是否在线。"""
    return {
        "service": "PaperTrace API",
        "status": "ok",
        "tasks_in_memory": len(TASKS),
    }


# ===========================================================
# 如何启动 / 如何测试
# ===========================================================
#
# 1) 启动开发服务器（在 backend/ 目录下、激活 venv 之后）：
#       uvicorn main:app --reload --port 8000
#
#    --reload 让 uvicorn 监听文件改动，改完代码自动重启
#    --port 8000 端口
#
#    成功后看到：
#       INFO:     Uvicorn running on http://127.0.0.1:8000
#
# 2) 浏览器打开自动文档：
#       http://localhost:8000/docs
#    FastAPI 会自动生成 Swagger UI，能直接在网页里点按钮试接口。
#
# 3) curl 测试：
#
#    a) 健康检查：
#       curl http://localhost:8000/
#
#    b) 提交分析任务：
#       curl -X POST http://localhost:8000/api/analyze \
#            -H "Content-Type: application/json" \
#            -d '{"query": "remote work productivity", "limit": 3}'
#
#       响应：{"task_id": "abc...", "status": "pending", "message": "..."}
#
#    c) 查询状态（每 2 秒一次）：
#       curl http://localhost:8000/api/task/abc...
#
#    d) 任务 done 后取结果：
#       curl http://localhost:8000/api/result/abc...
#
# 4) 常见报错：
#    - ModuleNotFoundError: No module named 'fetcher'
#        → 你不是在 backend/ 目录下启动 uvicorn 的
#    - 401 Unauthorized（DeepSeek）
#        → .env 里的 key 不对
#    - 后台任务一直 running 不动
#        → 看 uvicorn 控制台的日志，多半是 DeepSeek 限流或网络问题
# ===========================================================
