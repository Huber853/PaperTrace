# PaperTrace

学术论文矛盾发现工具 —— 输入一个研究问题，自动从 Semantic Scholar 拉论文，用 LLM 抽取论文中的「主张」，再两两判断这些主张之间是支持、矛盾还是无关，最后用热力图可视化矛盾矩阵，并自动生成综述段落。

> 计算机设计大赛参赛作品。从 0 到部署共分 10 个切片，每个切片都可以独立跑通。

## 演示流程

1. 用户在首页输入研究问题（例如 *remote work productivity*）
2. 后端异步执行：拉论文 → 抽主张 → 两两判定关系 → 构建 N×N 矩阵
3. 前端轮询任务状态，完成后展示：
   - 论文卡片列表
   - 主张三元组（subject / intervention / conclusion）
   - **ECharts 矛盾矩阵热力图**（红=矛盾、绿=支持、灰=无关，点击格子看判定理由）
   - **一键自动综述**（200-400 字、带 [N] 引用、打字机展示）

## 技术栈

- **后端**：Python 3.14 + FastAPI + SQLAlchemy 2.0 + SQLite + httpx + Pydantic v2 + DeepSeek API
- **前端**：Next.js 14 (App Router) + TypeScript + Tailwind CSS + axios + ECharts
- **数据源**：Semantic Scholar 公共 API
- **LLM**：DeepSeek `deepseek-chat`（OpenAI 兼容协议，强制 JSON 输出）

## 项目结构

```
papertrace/
├── backend/                    # FastAPI 后端
│   ├── fetcher.py              # 切片 2：Semantic Scholar 异步抓取
│   ├── database.py             # 切片 3：SQLAlchemy 2.0 模型 + 建库
│   ├── extractor.py            # 切片 4：DeepSeek 主张抽取（带校验/重试）
│   ├── contradiction.py        # 切片 5：两两判定 + N×N 矩阵 + 缓存
│   ├── main.py                 # 切片 6：FastAPI 接口（analyze/task/result/review）
│   ├── generator.py            # 切片 9：综述段落生成（few-shot prompt）
│   ├── requirements.txt        # 切片 10：部署依赖清单
│   └── .env.example            # 环境变量模板
├── frontend/                   # Next.js 14 前端
│   ├── app/page.tsx            # 切片 7：首页（搜索 + 提交）
│   ├── app/result/[taskId]/    # 切片 7：结果页（轮询 + 展示 + 综述）
│   ├── components/
│   │   └── ContradictionMatrix.tsx   # 切片 8：ECharts 矩阵热力图
│   └── lib/api.ts              # axios 封装
├── render.yaml                 # 切片 10：Render Blueprint（一键部署后端）
├── DEPLOY.md                   # 切片 10：部署上线手把手指南
└── README.md
```

## 本地开发

### 后端

```bash
cd backend

# 1. 激活虚拟环境
# Git Bash (Windows):
source venv/Scripts/activate
# PowerShell (Windows):
venv\Scripts\Activate.ps1
# macOS / Linux:
source venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 DeepSeek key
cp .env.example .env
# 编辑 .env，把 DEEPSEEK_API_KEY 改成你自己的

# 4. 启动
uvicorn main:app --reload --port 8000

# 5. 验证
# 浏览器打开 http://localhost:8000/docs 看 Swagger
```

### 前端

```bash
cd frontend

# 1. 安装依赖
npm install

# 2. 配置后端地址
cp .env.example .env.local
# 默认指向 http://localhost:8000，本地开发不用改

# 3. 启动
npm run dev

# 4. 浏览器打开 http://localhost:3000
```

## 部署上线

完整步骤见 [DEPLOY.md](./DEPLOY.md) ——
**GitHub** 存代码 → **Render** 跑后端 → **Vercel** 跑前端，全部免费档可跑通，包含故障排查 checklist。

## 开发进度

- [x] 切片 1：项目初始化与环境搭建
- [x] 切片 2：论文数据获取（Semantic Scholar）
- [x] 切片 3：数据库设计与存储
- [x] 切片 4：主张抽取（DeepSeek）
- [x] 切片 5：矛盾判定逻辑
- [x] 切片 6：FastAPI 后端接口
- [x] 切片 7：Next.js 前端搭建
- [x] 切片 8：矩阵热力图可视化
- [x] 切片 9：综述自动生成
- [x] 切片 10：部署上线
