# PaperTrace 部署上线手把手指南（切片 10）

> 目标：把 PaperTrace 部署到公网，让答辩评委用浏览器就能访问。
> 架构：**GitHub** 存代码 → **Render** 跑后端（FastAPI）→ **Vercel** 跑前端（Next.js）。
> 全部可以白嫖，不用绑定信用卡。

每一步都包括：
1. 在哪个网页操作
2. 点哪个按钮 / 填什么内容
3. 成功后应该看到什么
4. 常见报错和怎么修

---

## 第 0 步：你需要的账号（5 分钟）

| 平台 | 用途 | 注册地址 | 是否要邮箱验证 |
|------|------|----------|----------------|
| GitHub | 存代码 | <https://github.com/signup> | 是 |
| Render | 跑后端 | <https://render.com/register> | 是（可用 GitHub 一键登录） |
| Vercel | 跑前端 | <https://vercel.com/signup> | 是（可用 GitHub 一键登录） |
| DeepSeek | LLM API | <https://platform.deepseek.com> | 是 |

**强烈建议**：Render 和 Vercel 都用「Sign up with GitHub」一键登录，
因为后续要授权它们读你的 GitHub 仓库。

---

## 第 1 步：把代码推到 GitHub（10 分钟）

### 1.1 在 GitHub 网页建一个空仓库

1. 浏览器打开 <https://github.com/new>
2. 填表：
   - **Repository name**：`papertrace`
   - **Description**：`学术论文矛盾发现工具 / 计算机设计大赛作品`
   - **Public**（必须公开，否则 Render / Vercel 免费档不让连）
   - **不要勾**「Add a README file」/「Add .gitignore」/「Choose a license」
     （我们本地已经有这些文件了，勾上会冲突）
3. 点绿色的 **Create repository** 按钮

成功后你会看到一个空仓库页面，下方有一段「…or push an existing repository」的命令。**先别复制**，下一步用我给你的精确命令。

### 1.2 检查本地仓库状态

打开本地 `D:\papertrace` 目录，开 Git Bash / PowerShell，跑：

```bash
cd /d/papertrace
git status
```

**你应该看到**：当前在 `main` 分支，工作区干净（如果不干净，先 `git commit -am "wip"`）。

**重要：再确认一遍密钥没被提交**：

```bash
git log --all --full-history -- backend/.env
```

**应该输出空的**。如果有任何输出，立即停下来 —— 你的 DeepSeek key 已经进 git 历史了，要清理：
```bash
git rm --cached backend/.env
git commit -m "fix: remove leaked .env"
```
然后**马上去 DeepSeek 控制台重新生成一把新 key**。

### 1.3 关联 GitHub 远程仓库并 push

把 `<你的用户名>` 换成你的 GitHub 用户名：

```bash
git remote add origin https://github.com/<你的用户名>/papertrace.git
git branch -M main
git push -u origin main
```

第一次 push 时浏览器会跳出 GitHub 登录窗口，按提示完成授权。

**成功后你应该看到**：
- 控制台输出 `Branch 'main' set up to track 'origin/main'`
- 刷新 GitHub 仓库页面，能看到 `backend/`、`frontend/`、`README.md` 等文件
- **检查一遍**：仓库里**绝对不能有** `.env`、`venv/`、`node_modules/`、`papertrace.db`

**常见报错**：

| 报错 | 原因 | 解决 |
|------|------|------|
| `fatal: remote origin already exists` | 之前加过 remote | `git remote set-url origin <新地址>` |
| `error: src refspec main does not match any` | 本地分支不叫 main | `git branch -M main` 强制改名 |
| `Updates were rejected because the remote contains work...` | GitHub 仓库有内容（你不小心勾了 README） | 删掉 GitHub 仓库重建，或 `git pull --rebase origin main` 后再 push |
| 卡在 push 不动 | 国内网络问题 | 配 Git 走代理或换 SSH 协议 |

---

## 第 2 步：部署后端到 Render（15 分钟）

### 2.1 选部署方式：Blueprint vs Web Service

仓库根目录已经有 `render.yaml`，所以你有**两种选**：

- **方案 A（推荐）**：用 Blueprint，Render 读 `render.yaml` 自动建服务
- **方案 B**：手动 New Web Service，自己填表

下面两种都讲。

### 2.2 方案 A：用 Blueprint 一键部署

1. 浏览器打开 <https://dashboard.render.com>
2. 点右上角 **New +** → **Blueprint**
3. 在 **Connect a repository** 列表里选 `papertrace`
   （没看到？点 **Configure account** 授权 Render 访问这个仓库）
4. Render 会自动读取 `render.yaml` 并显示一个服务清单：
   - Service: `papertrace-backend`
   - 你会看到几个**红色的「Required」标签**，表示有变量需要你手填
5. 在 **DEEPSEEK_API_KEY** 输入框填上你的 DeepSeek key（`sk-...` 那一串）
6. **FRONTEND_ORIGIN** 这里**先随便填一个占位值** `https://placeholder.vercel.app`
   （前端还没部署，等部署完拿到真实域名再回来改）
7. 点底部 **Apply**

Render 开始构建。**成功后你应该看到**：
- 服务状态从 `Building` → `Live`
- 顶部出现一个绿色域名，类似 `https://papertrace-backend-xxxx.onrender.com`
- 浏览器打开这个域名，应该看到 JSON：`{"service":"PaperTrace API","status":"ok","tasks_in_memory":0}`
- 加 `/docs` 后缀能看到 FastAPI 自动生成的 Swagger UI

**记下这个后端域名**，下一步前端要用。

### 2.3 方案 B：手动 New Web Service

1. 点 **New +** → **Web Service**
2. 选 GitHub 仓库 `papertrace`
3. 填表：
   - **Name**：`papertrace-backend`
   - **Region**：`Singapore`（国内最快）
   - **Branch**：`main`
   - **Root Directory**：`backend`
   - **Runtime**：`Python 3`
   - **Build Command**：`pip install -r requirements.txt`
   - **Start Command**：`uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**：`Free`
4. 展开 **Advanced**：
   - **Health Check Path**：`/`
   - **Environment Variables**（点 Add Environment Variable 一条条加）：
     - `DEEPSEEK_API_KEY` = 你的 key
     - `DEEPSEEK_BASE_URL` = `https://api.deepseek.com`
     - `DEEPSEEK_MODEL` = `deepseek-chat`
     - `FRONTEND_ORIGIN` = `https://placeholder.vercel.app`（先占位）
     - `PYTHON_VERSION` = `3.11.10`
5. 点底部 **Create Web Service**

剩下的和方案 A 一样。

### 2.4 Render 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| Build 阶段 `ERROR: Could not find a version that satisfies the requirement ...` | requirements.txt 里某个包版本太新，Python 3.11 不支持 | 把 `PYTHON_VERSION` 改 `3.12.7` 重新部署 |
| Build 成功但 `Health check failed` | 服务没绑到 `0.0.0.0:$PORT`，一般是 start command 里漏了 `--host 0.0.0.0` | 改 Start Command 重新部署 |
| 一切正常但前端调接口 CORS 报错 | `FRONTEND_ORIGIN` 没填或填错（比如多了末尾斜杠） | 去 Environment 改一下，Render 会自动重启 |
| 第一次访问要等 30 秒才响应 | 免费档闲置 15 分钟会休眠，第一次唤醒慢 | 答辩前用 UptimeRobot 每 14 分钟 ping 一次 `/`；或答辩前先点开页面预热 |
| 部署后偶尔丢任务 | 内存 dict 存任务，Render 半夜自动重启会清空 | MVP 阶段可接受，未来改持久化（slice 6 的注释里讲了为什么这么取舍） |
| `papertrace.db` 重启后丢失 | SQLite 在容器临时盘上 | 启用 `render.yaml` 里注释掉的 disk 配置，并改 `DATABASE_URL` |

---

## 第 3 步：部署前端到 Vercel（10 分钟）

### 3.1 导入仓库

1. 浏览器打开 <https://vercel.com/new>
2. 在 **Import Git Repository** 里搜 `papertrace`，点右边 **Import**
   （没看到？点 **Adjust GitHub App Permissions** 给 Vercel 授权这个仓库）
3. **关键设置**（默认会蒙圈）：
   - **Framework Preset**：自动识别为 `Next.js` ✓
   - **Root Directory**：点 **Edit** 改成 `frontend`（这是最容易漏的一步，不改就部署根目录会失败）
   - **Build and Output Settings**：默认，不动
   - **Environment Variables**：点开，添加一条：
     - Name：`NEXT_PUBLIC_API_URL`
     - Value：第 2 步拿到的 Render 后端域名，例如 `https://papertrace-backend-xxxx.onrender.com`（**不要带末尾斜杠**）
4. 点底部 **Deploy**

Vercel 开始构建（大约 1-2 分钟）。

### 3.2 成功后

你会看到一个庆祝动画和一个 `https://papertrace-xxxx.vercel.app` 域名。点开它：
- 应该看到 PaperTrace 紫黑渐变首页
- 输入「remote work productivity」，limit 选 3，点提交
- 跳转到结果页 → 轮询 → 成功展示矩阵 + 综述按钮

**如果失败**，看下一节。

### 3.3 回头修 CORS

前端能加载但调接口时浏览器 console 报：
```
Access to XMLHttpRequest at 'https://papertrace-backend-xxxx.onrender.com/api/analyze'
from origin 'https://papertrace-xxxx.vercel.app' has been blocked by CORS policy
```

这是因为后端的 `FRONTEND_ORIGIN` 还是占位值。修法：

1. 复制 Vercel 给你的真实域名（**不要带末尾斜杠**）
2. 回 Render 控制台 → 你的服务 → **Environment**
3. 把 `FRONTEND_ORIGIN` 的值改成那个域名
4. 点 **Save Changes**，Render 自动重启（约 30 秒）
5. 刷新前端页面，再点提交，应该就通了

**进阶**：如果你既有 production 域名又有 preview 域名，用逗号分隔：
```
FRONTEND_ORIGIN=https://papertrace.vercel.app,https://papertrace-git-main-xxx.vercel.app
```

### 3.4 申请一个好看的子域名（可选）

Vercel 默认给你的是 `papertrace-xxxx-yourname.vercel.app`，
你可以改成 `papertrace.vercel.app`（如果没被别人占）：

1. 进项目 → **Settings** → **Domains**
2. 点 **Add**，输入 `你想要的名字.vercel.app`
3. 如果可用，立即生效；不可用就换一个名字试

---

## 第 4 步：预热 + 演示前检查（5 分钟）

答辩前 30 分钟做这些事，避免现场翻车：

```bash
# 1. 预热后端（让 Render 唤醒，避免冷启动）
curl https://papertrace-backend-xxxx.onrender.com/

# 2. 预热前端
curl https://papertrace-xxxx.vercel.app/

# 3. 真打一次完整流程，确认 LLM 余额还够
```

打开浏览器，搜一个有把握的题目（推荐用「remote work productivity」或「coffee caffeine cognitive performance」，
这两个 Semantic Scholar 数据稳定），等 30-60 秒看到结果，再点一次「生成综述」。

---

## 部署故障排查 Checklist

按顺序检查，命中哪条就照对应的修：

### A. GitHub
- [ ] 仓库是 **Public**？（私有仓库免费档拒绝连）
- [ ] 仓库根目录有 `backend/` 和 `frontend/` 两个子目录？
- [ ] 仓库**没有** `.env`、`venv/`、`node_modules/`、`*.db`？
  - 用 GitHub 网页搜一下 `DEEPSEEK_API_KEY` 确保密钥没泄露

### B. Render（后端）
- [ ] 服务状态是 **Live**（绿色），不是 Building/Failed？
- [ ] 直接打开 `https://你的后端.onrender.com/` 能看到 `{"service":"PaperTrace API",...}`？
- [ ] `/docs` 能打开 Swagger UI？
- [ ] Environment 里 5 个变量都有值（DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL / FRONTEND_ORIGIN / PYTHON_VERSION）？
- [ ] `FRONTEND_ORIGIN` 的值和 Vercel 给的域名**完全一致**（无末尾斜杠、协议是 `https`）？
- [ ] Logs 标签页里没有反复出现的红色 ERROR？

### C. Vercel（前端）
- [ ] **Root Directory** 设的是 `frontend`，不是空？
- [ ] **Environment Variables** 里有 `NEXT_PUBLIC_API_URL`？
- [ ] `NEXT_PUBLIC_API_URL` 是后端的完整 https URL，**没带末尾斜杠**？
- [ ] 部署历史里最新一次是 **Ready** 状态？
- [ ] 改完环境变量后**重新 Deploy 了一次**？（环境变量不会自动触发新部署）

### D. 浏览器侧（最后兜底）
- [ ] F12 打开 console，复现问题，看红色报错的第一行
- [ ] **CORS 报错** → 回到 B 检查 `FRONTEND_ORIGIN`
- [ ] **404** → 检查 `NEXT_PUBLIC_API_URL` 拼写
- [ ] **500** → 看 Render 的 Logs，多半是 DeepSeek key 错或余额不足
- [ ] **网络超时 / pending 卡住** → Render 在冷启动，等 30 秒刷新再试
- [ ] **Semantic Scholar 429** → 它在限流，换个查询词或等 60 秒重试
  （这是外部依赖问题，无法根治；建议演示前先成功跑一次相同查询，让结果走缓存）

---

## 一键预热脚本（可选，建议做）

把下面这段保存为 `tools/warmup.sh`（或 PowerShell 版），答辩前跑一次：

```bash
#!/usr/bin/env bash
BACKEND="https://papertrace-backend-xxxx.onrender.com"
FRONTEND="https://papertrace-xxxx.vercel.app"

echo "[1/3] 唤醒后端 ..."
curl -s "$BACKEND/" | head -c 200
echo

echo "[2/3] 唤醒前端 ..."
curl -s -o /dev/null -w "HTTP %{http_code}\n" "$FRONTEND/"

echo "[3/3] 真打一次 analyze（预热 LLM 链路）..."
curl -s -X POST "$BACKEND/api/analyze" \
  -H "Content-Type: application/json" \
  -d '{"query":"remote work productivity","limit":3}'
echo
echo "完成。等 60 秒再去前端演示。"
```

如果想做得更狠，把这段挂到 [UptimeRobot](https://uptimerobot.com) 免费监控，
每 14 分钟 ping 一次后端，让它永远不进入休眠。

---

## 写在最后

到这一步，PaperTrace 已经从 0 到 1 完整跑通：
代码结构 → 数据获取 → LLM 抽取 → 矩阵计算 → 可视化 → 自动综述 → 公网部署。

**答辩时建议突出三个差异化亮点**：
1. **结构化的矛盾矩阵**：不是「列论文」，是把每两篇之间的关系明确量化
2. **LLM 反向回流**：先抽 → 再判 → 再回流成段落，每一层都可解释
3. **省成本的工程优化**：subject 预筛 + 上三角 + 缓存，把 N² 调用砍掉一半以上

祝答辩顺利。
