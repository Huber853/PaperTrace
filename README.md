# PaperTrace · 学术论文矛盾发现与自动综述工具

> 输入一个研究问题，自动从学术索引抓论文 → 用 LLM 将摘要压缩为结构化「主张」→ 两两判定支持 / 矛盾 / 无关 → 可视化争论网络、生成带引用的综述段落，并给出后续研究方向建议。

**适用场景**：写论文做文献综述、立项前快速判断领域共识与分歧、答辩准备时找争论焦点。

---

## 核心处理流水线

整个系统的工作分为 7 个阶段，每个阶段对应一个独立模块：

```
用户输入查询
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ 阶段 1  fetcher.py + sources/                           │
│ 论文抓取：OpenAlex（主）→ 失败自动 fallback → arXiv（备）│
│ 文件级缓存，同一 query+limit 直接返回                    │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ 阶段 2  database.py                                     │
│ 入库：papers 表去重写入，拿到自增 id                      │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ 阶段 3  extractor.py                                    │
│ 主张抽取：DeepSeek 从每篇摘要抽 1~5 条结构化主张          │
│ 每条 = subject + intervention + conclusion + direction   │
│ Pydantic 校验 + JSON 重试 + 并发限速（Semaphore 4）      │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ 阶段 4  contradiction.py                                │
│ 两两判定：N 条主张 → N×N 关系矩阵                        │
│ support / contradict / unrelated + 置信度 + 理由         │
│ 五重优化：subject 过滤 → 批量判定 → 两级缓存 → 上三角 → 并发│
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌────────────────────┴────────────────────────────────────┐
│ 阶段 5  timeline.py        阶段 6  generator.py         │
│ 按年份聚合主张方向           生成 200~400 字学术综述       │
│ 正/负/中立场的时间演化       带 [N] 引用、token 用量统计   │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ 阶段 7  前端可视化                                       │
│ 矛盾矩阵热力图 · 力导向网络图 · 观点分布条 · 矛盾卡片    │
│ 研究方向建议 · AI 综述 · 导出 Markdown / BibTeX          │
└─────────────────────────────────────────────────────────┘
```

---

## 演示流程

1. 用户在首页输入一个研究问题（例如 *remote work productivity*）
2. 后端异步执行上述 7 个阶段，前端每 2 秒轮询任务状态
3. 完成后展示：
   - **指标卡片**：论文数、主张数、矛盾对数、支持对数
   - **观点分布条**：主张方向占比（支持 / 中立 / 反对）
   - **矛盾卡片**：高置信度矛盾对，附 LLM 给出的判定理由
   - **观点矛盾网络**：force-layout 力导向图，节点 = 论文，连线 = 论文间矛盾，按立场染色
   - **研究方向建议**：基于已发现的矛盾让 DeepSeek 给出待研究问题与方法路径
   - **论文列表 + 完整主张表 + 矛盾矩阵热力图**
   - **AI 综述段落**：200–400 字、带 `[N]` 引用、带 token 用量与耗时
   - **导出**：Markdown 综述报告 / BibTeX 文献列表

---

## 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 后端框架 | Python 3.14 + FastAPI + Pydantic v2 | 异步任务 + 强类型请求/响应模型 |
| ORM / 存储 | SQLAlchemy 2.0 + SQLite | 4 张表：papers / claims / contradictions / relation_cache |
| 异步 HTTP | httpx | 全异步，共享客户端连接池 |
| 数据源 | OpenAlex（主）+ arXiv（备） | 抽象基类 + 自动 fallback，解决国内 Semantic Scholar 限流问题 |
| LLM | DeepSeek `deepseek-chat` | OpenAI 兼容协议，强制 JSON 输出（`response_format`） |
| 前端框架 | Next.js 14（App Router）+ TypeScript | 首页 + 结果页，轮询任务状态 |
| 样式 | Tailwind CSS + Noto Sans SC / Noto Serif SC | 自定义设计 token，深色学术风 |
| HTTP 客户端 | axios | 前端 API 封装 |
| 可视化 | ECharts | N×N 矩阵热力图 + 力导向矛盾网络图 |

---

## 关键设计决策

### 1. 为什么先抽「主张」再判关系，而不是直接拿两段摘要比？

- 一篇论文可能同时有正向和负向发现（如远程办公"提升生产力但加剧孤独"），先拆成原子主张才能精确比对
- 拆完之后能在前端用热力图清晰展示"哪一对主张矛盾"
- 每条主张标准化为 4 字段（subject / intervention / conclusion / direction），机器可比对

### 2. LLM 输出为什么必须用 Pydantic 校验？

LLM 是概率机器：让它返回 JSON，99% 的时候听话，但偶尔会多塞字段、大小写不一致、用不在预设值里的同义词。Pydantic 在数据进库前做质检：
- `field_validator` 自动将 "POSITIVE" → "positive"、"increase" → "positive"
- JSON 解析失败自动重试一次，两次都失败返回空列表（不让上游崩）
- 永远不信任 LLM 输出格式

### 3. 矛盾判定的成本优化

对 20 篇论文（约 60 条 claim），朴素做法需要 C(60,2) = 1770 次 LLM 调用。通过五层优化将其砍到 100~200 次：

| 优化手段 | 效果 |
|---------|------|
| **Subject 词汇集合过滤** | 两条 claim 的 subject+intervention 完全无 token 交集 → 直接判 unrelated，砍掉 50%~70% |
| **批量判定**（每批 6 对） | 摊薄 system prompt 成本，输入 token 减少 60%~70% |
| **上三角 + 对角线短路** | 关系对称，只算 i<j；对角线直接填 support/1.0 |
| **两级缓存**（L1 内存 + L2 SQLite） | 内容指纹做 key，同一对 claim 只判一次，跨任务复用 |
| **asyncio 并发 + Semaphore 限流** | 充分利用 DeepSeek 并发额度（默认 8） |

**成本估算**：首次冷跑约 0.15 元/次（比朴素方案便宜 70%），第二次跑同 query 约 0 元。

### 4. 综述生成的「反向回流」策略

不是让 LLM 自己"发现分歧"（成本高、易漏、引用不准），而是：
1. 先通过阶段 3/4 把分析工作做完（已知哪些对 contradict、哪些对 support）
2. 把"预先识别的矛盾对"显式喂给 LLM
3. LLM 只需"把已知事实编织成段落"—— 成本低、引用准确、矛盾无遗漏

### 5. 异步任务模式

分析一次要做四件事（拉论文 → 抽主张 → 两两判定 → 入库），全跑完可能 30 秒到 2 分钟。采用 FastAPI `BackgroundTasks`：
- `POST /api/analyze` 立刻返回 task_id（瞬间响应）
- 真正的活儿在后台执行
- 前端拿到 task_id 后定时轮询 `/api/task/{task_id}`
- 任务状态用进程内内存 dict 存储（MVP 阶段取舍，生产环境可加 tasks 表）

---

## 数据库设计

4 张表，通过外键关联：

```
papers (论文)
  ├── id (PK, 自增)
  ├── paper_id (唯一索引，业务去重 key)
  ├── title, abstract, year, authors, citation_count
  │
  └── claims (主张)  ←── 外键 paper_id → papers.id
        ├── id (PK)
        ├── subject, intervention, conclusion, direction
        │
        └── contradictions (关系矩阵)  ←── 外键 claim_a_id / claim_b_id → claims.id
              ├── relation (support / contradict / unrelated)
              └── confidence (0.0 ~ 1.0)

relation_cache (判定缓存，独立于任务)
  ├── pair_hash (PK, sha256 内容指纹)
  ├── sig_lo / sig_hi (排序后的 claim 指纹)
  ├── model (记录哪个模型判的，换模型可整批失效)
  └── relation, confidence, reason
```

---

## 后端接口

| Method | Path | 说明 |
| --- | --- | --- |
| POST | `/api/analyze` | 提交分析任务，返回 `task_id` |
| GET | `/api/task/{id}` | 查询任务状态 / 进度 / 错误 |
| GET | `/api/result/{id}` | 拉取完整结果（papers / claims / matrix / timeline） |
| GET | `/api/review/{id}` | 触发或读取 LLM 综述（带缓存、token、模型名） |
| POST | `/api/chat` | 通用 DeepSeek 代理（前端用它做研究方向建议） |
| POST | `/api/export/{id}?format=md\|bib` | 导出综述报告（Markdown）或文献列表（BibTeX） |

`/api/chat` 是**透传式**代理：前端把 `messages` / `temperature` / `response_format` 直接发过来，后端只负责注入 API Key 并转发，避免把 DeepSeek key 暴露到浏览器。

---

## 项目结构

```
papertrace/
├── backend/                          FastAPI 后端
│   ├── main.py                       全部 HTTP 接口（6 个路由 + Pydantic 模型）
│   ├── fetcher.py                    论文抓取调度（多源 fallback + 缓存适配层）
│   ├── sources/
│   │   ├── base.py                   数据源抽象基类 BaseSource
│   │   ├── openalex.py               OpenAlex 适配器（REST API，免费无需 key）
│   │   └── arxiv.py                  arXiv 适配器（Atom feed，feedparser 解析）
│   ├── extractor.py                  主张抽取（DeepSeek + Pydantic 校验 + 重试）
│   ├── contradiction.py              两两判定 + N×N 矩阵 + 两级缓存 + 批量优化
│   ├── timeline.py                   按年份聚合主张方向（纯数据转换，无网络/LLM）
│   ├── generator.py                  综述生成 + 通用 DeepSeek chat 封装
│   ├── database.py                   SQLAlchemy 2.0 模型（4 张表）+ 建库 + 会话工具
│   ├── cache.py                      文件级缓存（JSON 文件，按 query+limit 哈希）
│   ├── http_client.py                共享 httpx 客户端（连接池复用）
│   ├── requirements.txt              依赖（版本写死，保证部署可复现）
│   └── .env.example                  环境变量模板
│
├── frontend/                         Next.js 14 前端
│   ├── app/
│   │   ├── layout.tsx                根布局（Noto 字体 + favicon）
│   │   ├── page.tsx                  首页（搜索框 + 提交）
│   │   ├── result/[taskId]/page.tsx  结果页（轮询 + 全部模块编排）
│   │   └── globals.css               设计 token 与基础样式
│   ├── components/
│   │   ├── OpinionBar.tsx            观点占比堆叠条
│   │   ├── ContradictionCards.tsx    高置信度矛盾对卡片
│   │   ├── DebateNetwork.tsx         力导向矛盾网络（ECharts graph）
│   │   ├── RecommendationPanel.tsx   研究方向建议（调 /api/chat 实时生成）
│   │   ├── AiReview.tsx              带 [N] 引用高亮的综述段落
│   │   ├── ContradictionMatrix.tsx   N×N 矩阵热力图（ECharts heatmap）
│   │   └── LoadingSteps.tsx          任务进行中分步动画
│   ├── lib/api.ts                    axios 封装 + 全部 TypeScript API 类型
│   └── tailwind.config.ts            设计 token（颜色 / 字号 / 圆角）
│
├── render.yaml                       Render Blueprint（一键部署后端）
├── DEPLOY.md                         部署上线手把手指南（含故障排查 checklist）
└── README.md
```

---

## 本地开发

### 后端

```bash
cd backend

# 1) 激活虚拟环境
source venv/Scripts/activate          # Git Bash on Windows
venv\Scripts\Activate.ps1             # PowerShell on Windows
source venv/bin/activate              # macOS / Linux

# 2) 安装依赖
pip install -r requirements.txt

# 3) 配置 DeepSeek
cp .env.example .env
# 编辑 .env 把 DEEPSEEK_API_KEY 改成你自己的

# 4) 启动
uvicorn main:app --reload --port 8000

# 5) 验证
# 浏览器打开 http://localhost:8000/docs 看 Swagger UI
```

### 前端

```bash
cd frontend

# 1) 安装依赖
npm install

# 2) 配置后端地址
cp .env.example .env.local
# 默认指向 http://localhost:8000，本地开发不用改

# 3) 启动
npm run dev

# 4) 浏览器打开 http://localhost:3000
```

---

## 设计系统

前端遵循一套深色学术风的视觉语言，通过 `tailwind.config.ts` 集中管理：

- **配色**：底色 `#111428`（暖深紫）+ 表面 `#1A1E3A`，立场色 支持 `#4ADE80` / 反对 `#FFB547` / 混合 `#B4AEFF`
- **字体**：标题 / 正文用 Noto Sans SC，综述段落用 Noto Serif SC，数字用等宽 `.num`
- **氛围**：首页最外层使用双径向光晕（左上紫、右下琥珀）营造层次感
- **交互**：所有可视化（矩阵 / 网络图）的悬浮提示、强调高亮颜色都来自同一套 token，避免色彩漂移

---

## 部署上线

完整步骤见 [DEPLOY.md](./DEPLOY.md) ——

**GitHub** 存代码 → **Render** 跑后端 → **Vercel** 跑前端，全部免费档可跑通，附故障排查 checklist。

---

## License

仅作教学与赛事演示用途。论文文本、摘要等数据版权归原始作者与出版方所有。
