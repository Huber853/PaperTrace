# PaperTrace 生产级轻量 Agent Harness 设计

## 1. 背景

PaperTrace 当前是一条由 FastAPI `BackgroundTasks` 驱动的固定分析流水线：论文检索、主张抽取、关系分类、时间线、综述与导出依次执行。现有模块已经具备清晰的学术分析能力，但任务状态保存在进程内 `TASKS` 字典中，模型调用、工具调用、阶段状态、中间产物和评测尚未形成统一的 Agent Harness。

本设计将现有业务能力包装为工具，在其外部增加可持久化、可观察、可恢复、可评测的单 Agent Harness。目标是达到适合 PaperTrace 当前负载的生产工程水平，同时避免为多线程、高并发和分布式一致性引入不必要复杂度。

## 2. 目标与非目标

### 2.1 目标

- 使用“确定性外环 + 受限 Agent 内环”的混合范式。
- 将 Run、Step、ToolCall、Artifact 和 Event 持久化。
- 让模型在受控阶段内自主选择工具、参数和补充证据策略。
- 支持服务重启后的任务恢复、失败重试、取消和等待用户输入。
- 提供统一 Tool Contract、Hook 生命周期和 Run Policy。
- 在前端展示阶段进度和可审计 Trace，不展示隐藏思维链。
- 建立无需 API Key 即可运行的离线 Eval Harness。
- 生产环境采用 Render Web Service、单 Worker 和 PostgreSQL；本地支持 SQLite。

### 2.2 非目标

- 不实现多 Agent 协作、Agent 间辩论或复杂仲裁。
- 不引入 Redis、Celery、Kafka、WebSocket 或分布式锁。
- 不支持无限递归、自修改计划或无边界工具调用。
- 不重写现有论文检索、主张抽取和关系分类算法。
- 不在首版实现全文 PDF RAG；现有摘要级证据能力保持不变。
- 不对高并发、水平扩容和 exactly-once 语义进行过度优化。

## 3. 方案选择

评估过三种范式：

1. 纯工作流 DAG：稳定且成本低，但模型没有实质决策空间。
2. 纯 ReAct Agent：自主性强，但容易重复调用、消耗失控，流程完整性难保证。
3. 混合 Harness：外层状态机保证研究流程，阶段内运行受限 ReAct Loop。

选择第三种。它能复用 PaperTrace 已有流水线，同时让系统具备真实但可控的 Agent 行为。

## 4. 总体架构

```text
Next.js Frontend
       |
       v
FastAPI API -----> Agent Run Store <----- Single Worker
                         |                      |
                         |                      v
                         |                Agent Harness
                         |                 |  |  |  |
                         |                 |  |  |  +-- Run Policy
                         |                 |  |  +----- Hook Bus
                         |                 |  +-------- Tool Registry
                         |                 +----------- Phase + Agent Loop
                         |
                         +---- Runs / Steps / ToolCalls / Artifacts / Events
```

FastAPI 只负责请求验证、创建 Run、读取状态和接收用户输入。Worker 从数据库领取待执行 Run，并调用 Agent Harness。Harness 是唯一可以推进 Agent 状态的组件。

## 5. 模块边界

新增 `backend/agent/`：

| 模块 | 职责 |
| --- | --- |
| `schemas.py` | Run、Action、Step、ToolResult、Artifact 等 Pydantic 契约 |
| `models.py` | Agent 相关 SQLAlchemy 模型与枚举 |
| `repository.py` | Agent 数据访问和事务边界 |
| `tools.py` | AgentTool、ToolRegistry 及现有业务能力适配器 |
| `hooks.py` | Hook 协议、HookBus 和内置 Hook |
| `policies.py` | 步数、重复调用、重试、超时和阶段产物约束 |
| `phases.py` | 研究阶段定义、阶段输入和完成条件 |
| `model_provider.py` | DeepSeek 模型适配与结构化 Action 生成 |
| `loop.py` | 阶段内部受限 Agent Loop |
| `harness.py` | Run 生命周期、阶段推进、恢复、暂停和终止 |
| `worker.py` | 单 Worker 轮询、领取和执行 Run |

现有 `fetcher.py`、`extractor.py`、`contradiction.py`、`timeline.py` 和 `generator.py` 继续负责领域逻辑。工具适配器负责把这些函数转换为统一 Tool Contract。

## 6. 数据模型

### 6.1 `agent_runs`

保存一次完整研究任务：

- `id`：UUID。
- `query`、`paper_limit`、`refresh`：用户输入。
- `status`：`queued`、`running`、`waiting_input`、`completed`、`failed`、`cancelled`。
- `current_phase`：当前研究阶段。
- `policy_json`：本次 Run 的预算配置快照。
- `input_context_json`、`pending_question`：用户补充信息和当前待回答问题。
- `step_count`、`token_usage`：累计预算用量。
- `error_code`、`error_message`：最终错误。
- `created_at`、`updated_at`、`started_at`、`finished_at`。

### 6.2 `agent_steps`

记录每轮模型决策和观察：

- `run_id`、`sequence`、`phase`。
- `action_type`、`action_summary`。
- `status`、`started_at`、`finished_at`。
- `model_name`、`input_tokens`、`output_tokens`。
- `error_code`、`error_message`。

只保存简洁、面向审计的决策摘要，不保存或要求模型输出隐藏思维链。

### 6.3 `tool_calls`

记录工具调用：

- `step_id`、`tool_name`。
- `arguments_json`、`arguments_hash`。
- `status`、`result_summary`、`artifact_ids_json`。
- `duration_ms`、`retry_count`。
- `error_code`、`error_message`。

### 6.4 `agent_artifacts`

保存版本化中间产物：

- `run_id`、`kind`、`version`。
- `content_json` 或已有领域对象的引用。
- `source_step_id`、`created_at`。

首版 Artifact 类型为：`research_plan`、`paper_set`、`claim_set`、`evidence_graph`、`review_draft`、`verification_report` 和 `final_report`。

### 6.5 `agent_events`

保存前端增量读取的事件：

- `run_id`、单调递增 `sequence`。
- `event_type`、`phase`、`message`、`payload_json`。
- `created_at`。

现有 `papers`、`claims` 和 `relation_cache` 保留。数据库变更通过 Alembic 版本化迁移管理。

## 7. Run 状态机与恢复

```text
queued -> running -> waiting_input -> running -> completed
                    \                         \
                     +-------------------------> failed
                     +-------------------------> cancelled
```

Run 的研究阶段固定为：

```text
PLAN -> DISCOVER -> EXTRACT -> ANALYZE -> SYNTHESIZE -> VERIFY -> FINALIZE
```

单 Worker 不实现复杂租约。Worker 每次通过一个短事务领取创建时间最早的 `queued` Run，并立即将其标记为 `running`。Worker 启动时将遗留的 `running` Run 恢复为 `queued`。Harness 从最近完成的 Phase Artifact 和 Step 继续执行。已完成的、参数哈希相同的幂等工具调用可复用，避免重启后重复消耗。

取消采用协作式语义：API 将 Run 标记为 `cancelled`，Harness 在当前模型或工具调用结束后的下一个 Step 边界停止。失败重试复用同一个 Run，清除终态错误并从最近检查点重新进入 `queued`。用户提交补充信息后写入 `input_context_json`，清除 `pending_question`，并把 `waiting_input` Run 恢复为 `queued`。

## 8. Agent Loop

每个 Phase 内执行以下循环：

1. 加载 Run、当前 Phase、已存在 Artifact 和允许调用的工具。
2. 触发 `before_step` 与 `before_model`。
3. 请求模型生成结构化 Action。
4. 使用 Pydantic 校验 Action，并执行 Policy 检查。
5. 执行工具、完成阶段、请求用户输入或终止 Run。
6. 持久化 Step、ToolCall、Artifact 和 Event。
7. 检查预算、阶段完成条件和下一步状态。

模型只能生成四种 Action：

```python
ToolAction(tool_name, arguments, rationale)
CompletePhase(summary, artifact_ids)
RequestInput(question, reason)
AbortRun(error_code, message)
```

`rationale` 是简短决策摘要，用于 Trace 和调试。模型输出不包含详细思维链。

### 8.1 阶段职责

| Phase | Agent 自主范围 | 必须产物 |
| --- | --- | --- |
| `PLAN` | 拆解问题、形成关键词和分析策略 | `research_plan` |
| `DISCOVER` | 搜索、调整关键词、补充论文 | `paper_set` |
| `EXTRACT` | 选择论文并提取结构化主张 | `claim_set` |
| `ANALYZE` | 分类支持、矛盾和无关关系 | `evidence_graph` |
| `SYNTHESIZE` | 生成综述草稿和研究方向 | `review_draft` |
| `VERIFY` | 检查引用、覆盖率和证据归属 | `verification_report` |
| `FINALIZE` | 基于验证结果修订并封装结果 | `final_report` |

### 8.2 三类循环

- 执行 Loop：模型决策、工具调用和观察。
- 验证 Loop：验证不通过时最多回退修订一次。
- 恢复 Loop：服务重启后从最后检查点继续。

不实现无限循环、递归规划或 Agent 自行创建 Agent。

## 9. Tool Contract

```python
class AgentTool:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    allowed_phases: set[AgentPhase]

    async def execute(
        self,
        context: ToolContext,
        arguments: BaseModel,
    ) -> ToolResult: ...
```

统一返回：

```python
ToolResult(
    status="success | partial | failed",
    data={...},
    summary="供下一轮模型决策使用的短摘要",
    artifact_ids=[...],
    metrics={"duration_ms": 1200, "token_usage": 0},
    error=None,
)
```

首版工具：

- `search_papers`
- `extract_claims`
- `classify_relations`
- `build_timeline`
- `generate_review`
- `recommend_directions`
- `verify_evidence`
- `export_report`

`ToolRegistry` 负责工具查找、输入校验、Phase 白名单、重复调用检测和统一错误映射。推荐研究方向从前端 Prompt 迁回后端工具，开放式 `/api/chat` 不再作为核心业务入口。

## 10. Hook 生命周期

```text
before_run
  before_phase
    before_step
      before_model -> after_model / on_model_error
      before_tool  -> after_tool  / on_tool_error
      on_artifact_saved
    after_step / on_step_error
  after_phase / on_phase_error
after_run / on_run_error
```

内置 Hook：

| Hook | 职责 |
| --- | --- |
| `PersistenceHook` | 持久化 Run、Step、ToolCall 和 Artifact |
| `TraceHook` | 记录耗时、模型、Token、动作摘要和错误 |
| `BudgetHook` | 在模型或工具调用前检查预算 |
| `ProgressHook` | 写入前端可读取的进度事件 |
| `RecoveryHook` | 标记检查点和可复用调用 |

Hook 可以观察执行，或在执行前返回 `allow`、`reject`、`pause`。Hook 不直接修改 Agent State；所有状态变化由 Harness 完成。

## 11. Policy

默认策略：

```python
RunPolicy(
    max_steps=20,
    max_steps_per_phase=4,
    max_tool_repeats=2,
    tool_retry_count=1,
    verification_revisions=1,
    phase_timeout_seconds=300,
)
```

补充规则：

- Phase 必须生成指定 Artifact 才能完成。
- 相同工具和相同参数优先复用已有成功结果。
- `VERIFY` 只检查已有证据，不能新增论文或 Claim。
- 达到预算时，使用已有 Artifact 生成带警告的部分结果。
- 查询含糊或检索结果明显偏题时进入 `waiting_input`。

## 12. Model Provider

`ModelProvider` 负责结构化 Action、Token 统计和模型错误映射。首版复用 DeepSeek `deepseek-chat`，并保留未来适配 OpenAI、Claude 或本地模型的边界。领域模块不直接依赖具体 Provider。

## 13. API

新增：

```http
POST   /api/agent/runs
GET    /api/agent/runs/{run_id}
GET    /api/agent/runs/{run_id}/events
GET    /api/agent/runs/{run_id}/trace
POST   /api/agent/runs/{run_id}/input
POST   /api/agent/runs/{run_id}/cancel
POST   /api/agent/runs/{run_id}/retry
GET    /api/agent/runs/{run_id}/result
GET    /api/agent/runs/{run_id}/export
```

现有 `/api/analyze` 在迁移期内部转调创建 Run，现有任务状态和结果接口保持兼容。前端稳定迁移后，可在后续版本删除 `TASKS` 和旧任务接口。

Events 使用 `after_seq` 增量轮询，不引入 SSE 或 WebSocket：

```http
GET /api/agent/runs/{run_id}/events?after_seq=20
```

## 14. 前端体验

结果页保留论文、Claim、矛盾矩阵、网络、时间线、综述和导出视图，并新增紧凑的 Agent 轨迹区域：

- 展示七个 Phase 的完成状态。
- 展示当前动作、工具、耗时和产物数量。
- 支持取消和失败重试。
- `waiting_input` 时展示补充问题输入框。
- Trace 展示决策摘要和错误，不展示隐藏思维链。
- 轮询 Event 时使用 `after_seq`，避免重复拉取完整历史。

## 15. 错误与降级

| 错误类型 | 处理 |
| --- | --- |
| `validation_error` | 阻止执行，并把校验错误作为下一轮可修正观察 |
| `tool_error` | 自动重试一次，仍失败则保留部分结果 |
| `provider_error` | 记录模型错误，Run 可手动重试 |
| `insufficient_evidence` | 补充检索或进入 `waiting_input` |
| `policy_exceeded` | 停止 Loop，基于已有 Artifact 降级完成 |

错误记录包含错误码、阶段、工具和用户可读消息，不记录 API Key、Authorization Header 或完整敏感请求。

## 16. Eval Harness

新增 `backend/evals/`：

```text
evals/
  cases/
    smoke.json
    relations.json
  datasets.py
  metrics.py
  runner.py
  fakes.py
  reports/
```

评测分三层：

1. 单元测试：状态跳转、预算、Hook 顺序、工具校验和恢复。
2. 离线回放：固定模型响应和检索结果，验证完整 Run 可重复运行。
3. 可选在线评测：调用真实 DeepSeek 与论文源，默认不在 CI 中运行。

核心指标：

- Run 完成率、平均步数和恢复成功率。
- 工具成功率和重复调用率。
- Claim 结构有效率。
- 关系分类准确率。
- 引用覆盖率和无证据论断比例。
- 报告必需章节完整率。
- Token、耗时和调用次数。

命令：

```powershell
python -m evals.runner --suite smoke
python -m evals.runner --suite relations --live
```

输出 JSON 和 Markdown 报告。离线套件不需要 API Key。

## 17. 部署

生产环境：

- Render FastAPI Web Service。
- Render 单实例 Worker。
- Render PostgreSQL。
- Vercel Next.js 前端。

本地默认 SQLite，并默认启用 API 进程内的轻量 Worker；设置 `AGENT_EMBEDDED_WORKER=false` 后可改为显式启动独立 Worker。生产环境固定设置为 `false`，始终使用独立 Worker。

更新 `render.yaml`、`.env.example`、README 和架构说明。GitHub Actions 运行后端测试、离线 smoke eval、Python 编译检查和前端构建。

## 18. 验收标准

1. 新 Run 状态不依赖 `TASKS`。
2. Worker 或 Web Service 重启后，未完成 Run 可以从检查点恢复。
3. 每次模型与工具调用都有可查询 Trace。
4. 前端可以看到阶段进度、错误和等待输入状态。
5. 报告经过证据验证，并可在预算或工具失败时输出部分结果。
6. 离线 Eval 无 API Key 可运行并输出报告。
7. 现有分析结果视图和导出能力继续工作。
8. 后端测试、Python 编译、前端构建和 CI 通过。

## 19. 实施顺序

1. 建立 Agent 数据模型、迁移和 Repository。
2. 建立 Tool Contract、ToolRegistry 和现有能力适配器。
3. 实现 Hook、Policy、Phase、Loop 和 Harness。
4. 实现 Worker、恢复和 Agent API。
5. 迁移现有分析入口和研究方向生成。
6. 增加前端 Agent 轨迹、等待输入、取消与重试。
7. 增加 Eval Harness、测试、CI 和部署配置。
8. 更新文档，完成端到端验证后发布 GitHub PR。
