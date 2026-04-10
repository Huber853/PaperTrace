# PaperTrace

学术论文矛盾发现工具 —— 输入一个研究问题，自动从 Semantic Scholar 拉论文，用 LLM 抽取论文中的"主张"，再两两判断这些主张之间是支持、矛盾还是无关，最后用热力图可视化矛盾矩阵，并自动生成综述。

## 项目结构

```
papertrace/
├── backend/         # FastAPI 后端
│   └── venv/        # Python 虚拟环境（不提交到 git）
└── frontend/        # Next.js 前端（后续切片创建）
```

## 技术栈

- **后端**：Python 3.14 + FastAPI + SQLAlchemy + SQLite + DeepSeek API
- **前端**：Next.js 14 (App Router) + TypeScript + Tailwind + ECharts
- **数据源**：Semantic Scholar API

## 快速开始（后端）

```bash
# 1. 进入后端目录
cd backend

# 2. 激活虚拟环境
# Windows (Git Bash):
source venv/Scripts/activate
# Windows (PowerShell):
venv\Scripts\Activate.ps1
# macOS / Linux:
source venv/bin/activate

# 3. 启动开发服务器（后续切片实现 main.py 后再用）
# uvicorn main:app --reload
```

## 开发进度

- [x] 切片 1：项目初始化与环境搭建
- [ ] 切片 2：论文数据获取（Semantic Scholar）
- [ ] 切片 3：数据库设计与存储
- [ ] 切片 4：主张抽取（DeepSeek）
- [ ] 切片 5：矛盾判定逻辑
- [ ] 切片 6：FastAPI 后端接口
- [ ] 切片 7：Next.js 前端搭建
- [ ] 切片 8：矩阵热力图可视化
- [ ] 切片 9：综述自动生成
- [ ] 切片 10：部署上线
