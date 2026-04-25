# PaperTrace · 学术论文矛盾发现工具

> 输入一个研究问题，自动从学术索引拉论文 → 用 LLM 把论文压成可比较的「主张」→ 两两判定支持 / 矛盾 / 无关 → 可视化争论网络、生成带引用的综述段落，并给出后续研究方向建议。

适用场景：写论文做文献综述、立项前快速判断领域共识与分歧、答辩准备时找争论焦点。

---

## 演示流程

1. 用户在首页输入一个研究问题（例如 *remote work productivity*）
2. 后端异步执行：抓论文 → 抽主张 → 两两判关系 → 拼 N×N 矩阵 → 按年份聚合时间轴
3. 前端轮询任务状态，完成后展示：
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

| 层 | 选型 |
| --- | --- |
| 后端框架 | Python 3.14 + FastAPI + Pydantic v2 |
| ORM / 存储 | SQLAlchemy 2.0 + SQLite（本地）|
| 异步 HTTP | httpx |
| 数据源 | OpenAlex（主）+ arXiv（备）|
| LLM | DeepSeek `deepseek-chat`（OpenAI 兼容协议，强制 JSON 输出）|
| 前端框架 | Next.js 14（App Router）+ TypeScript |
| 样式 | Tailwind CSS（自定义设计 token）+ Noto Sans SC / Noto Serif SC |
| HTTP 客户端 | axios |
| 可视化 | ECharts（矩阵热力图 + 力导向网络图）|

---

## 项目结构

```
papertrace/
├── backend/                          FastAPI 后端
│   ├── main.py                       全部 HTTP 接口
│   ├── fetcher.py                    论文抓取调度（OpenAlex 失败回落 arXiv）
│   ├── sources/
│   │   ├── base.py                   数据源抽象基类
│   │   ├── openalex.py               OpenAlex 适配
│   │   └── arxiv.py                  arXiv 适配
│   ├── extractor.py                  DeepSeek 主张抽取（带 JSON schema 校验 + 重试）
│   ├── contradiction.py              两两判定 + N×N 矩阵 + 缓存
│   ├── timeline.py                   按年份聚合主张方向
│   ├── generator.py                  综述段落 + 通用 DeepSeek 代理
│   ├── database.py                   SQLAlchemy 模型 + 建库
│   ├── cache.py / cache/             文件级缓存
│   ├── http_client.py                共享 httpx 客户端
│   └── requirements.txt              部署依赖（版本写死）
│
├── frontend/                         Next.js 14 前端
│   ├── app/
│   │   ├── layout.tsx                根布局（Noto 字体 + favicon）
│   │   ├── page.tsx                  首页（搜索框 + 提交）
│   │   ├── result/[taskId]/page.tsx  结果页（轮询 + 全部模块编排）
│   │   ├── globals.css               设计 token 与基础样式
│   │   ├── icon.svg / apple-icon.svg favicon
│   ├── components/
│   │   ├── OpinionBar.tsx            观点占比堆叠条
│   │   ├── ContradictionCards.tsx    高置信度矛盾对卡片
│   │   ├── DebateNetwork.tsx         力导向矛盾网络（论文节点）
│   │   ├── RecommendationPanel.tsx   研究方向建议（DeepSeek 实时生成）
│   │   ├── AiReview.tsx              带引用高亮的综述
│   │   ├── ContradictionMatrix.tsx   N×N 矩阵热力图（兜底详图）
│   │   └── LoadingSteps.tsx          任务进行中分步动画
│   ├── lib/api.ts                    axios 封装 + 全部 API 类型
│   ├── tailwind.config.ts            设计 token（颜色 / 字号 / 圆角）
│   └── README.md                     前端独立说明
│
├── render.yaml                       Render Blueprint（一键部署后端）
├── DEPLOY.md                         部署上线手把手指南
└── README.md
```

---

## 后端接口一览

| Method | Path | 说明 |
| --- | --- | --- |
| POST | `/api/analyze` | 提交分析任务，返回 `task_id` |
| GET | `/api/task/{id}` | 查询任务状态 / 进度 / 错误 |
| GET | `/api/result/{id}` | 拉取完整结果（papers / claims / matrix / timeline）|
| GET | `/api/review/{id}` | 触发或读取 LLM 综述（带缓存、token、模型名）|
| POST | `/api/chat` | 通用 DeepSeek 代理（前端用它做研究方向建议）|
| GET | `/api/export/{id}?format=md\|bib` | 导出综述报告（Markdown）或文献列表（BibTeX）|

`/api/chat` 是一个**透传式**代理：前端把 `messages` / `temperature` / `response_format` 直接发过来，后端只负责注入 API Key 并转发，避免把 DeepSeek key 暴露到浏览器。

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
# 浏览器打开 http://localhost:8000/docs 看 Swagger
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

## 设计系统简介

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
