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
import traceback                              # 后台任务出错时打全栈
import uuid                                   # 生成 task_id
from datetime import datetime, timezone
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
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
from generator import generate_review


# ===== 创建 FastAPI 应用 =====
app = FastAPI(
    title="PaperTrace API",
    description="学术论文矛盾发现工具——从研究问题到矛盾矩阵和综述。",
    version="0.1.0",
)


# ===== CORS 配置 =====
# 前端在 localhost:3000 跑，后端在 localhost:8000 跑，跨域必须放行
# 部署时把 NEXT_PUBLIC 的真实域名加进 origins 即可
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
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


class ResultResponse(BaseModel):
    """GET /api/result/{task_id} 的响应。"""
    task_id: str
    query: str
    papers: list[PaperOut]
    claims: list[ClaimOut]
    matrix: list[list[RelationOut]]   # claims 顺序对齐
    stats: dict


class ReviewResponse(BaseModel):
    """GET /api/review/{task_id} 的响应。"""
    task_id: str
    review: str
    cached: bool  # True 表示这次是从缓存取的


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
async def _run_analysis(task_id: str, query: str, limit: int):
    """
    后台任务函数。
    被 BackgroundTasks 调用，在 HTTP 响应发出去之后才开始执行。
    任何一步出错都会被捕获并更新任务状态。
    """
    try:
        _update_task(task_id, status="running", progress="正在搜索论文 ...")

        # === 第 1 步：拉论文 ===
        papers_raw = await search_papers(query, limit=limit)
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
        matrix = await build_matrix(claims_for_matrix)

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
    background_tasks.add_task(_run_analysis, task_id, req.query, req.limit)

    return AnalyzeResponse(
        task_id=task_id,
        status="pending",
        message="任务已提交，请用 /api/task/{task_id} 查询进度",
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

    # 缓存命中：直接返回
    if task.get("review"):
        return ReviewResponse(task_id=task_id, review=task["review"], cached=True)

    # 首次生成：调 generator
    result = task["result"]
    try:
        review_text = await generate_review(
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

    # 存缓存（下次就不用再调 API 了）
    task["review"] = review_text
    task["updated_at"] = _now_iso()

    return ReviewResponse(task_id=task_id, review=review_text, cached=False)


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
