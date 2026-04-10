"""
PaperTrace - 切片 2：论文数据获取模块
=====================================

作用：从 Semantic Scholar 公开 API 拉取论文元数据。
被谁调用：稍后切片 6 的 FastAPI 后端会调用 search_papers()，把结果写进数据库。

----------------------------------------------------
新手必读：什么是 async / await ？
----------------------------------------------------
普通函数（同步）调一个网络请求时，Python 会"傻等"几百毫秒，期间什么都不能做。
如果要拉 20 篇论文，就要等 20 次，慢得要命。

async 函数（异步）告诉 Python：
"我现在要等网络了，你先去干别的，等数据回来再回来叫我。"
这样你就能在等待 A 请求时同时发出 B 请求，速度暴涨。

- async def 定义一个"协程函数"
- await 表示"在这里等一个异步操作完成，期间让出控制权"
- 协程必须由 asyncio.run() 或别的协程驱动，不能直接当普通函数调

类比：去食堂打饭。同步=排一队等一个窗口；异步=同时点 5 个窗口的饭，谁好了先拿谁。
"""

# ===== 导入区 =====
import asyncio                # Python 内置的异步事件循环库
import sys                    # 用于在 Windows 上修正控制台编码
import httpx                  # 现代异步 HTTP 客户端，比 requests 更适合 async
from pprint import pprint     # 美化打印字典/列表，方便调试观察结构
from typing import Optional   # 类型提示，告诉别人这个变量可能是 None

# Windows 控制台默认是 GBK，会让中文 print 变乱码。强制 UTF-8。
# Python 3.7+ 支持 reconfigure；try/except 兜底防止低版本报错
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


# ===== 常量区 =====
# Semantic Scholar 的论文搜索接口（公开 API，无需 key 也能用，但有限流）
SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

# 我们想从每篇论文里取出来的字段（API 用逗号分隔的字符串接收）
# paperId 是 SS 内部唯一 ID；其余字段就是论文的常用元信息
PAPER_FIELDS = "paperId,title,abstract,year,authors,citationCount"

# 网络请求超时时间（秒）。Semantic Scholar 偶尔会慢，给宽一点
REQUEST_TIMEOUT = 30.0

# 限流时（HTTP 429）的重试次数
MAX_RETRIES = 3


# ===== 核心函数 =====
async def search_papers(query: str, limit: int = 20) -> list[dict]:
    """
    从 Semantic Scholar 异步搜索论文。

    参数:
        query: 搜索关键词，例如 "remote work productivity"
        limit: 最多返回多少篇论文，默认 20，Semantic Scholar 单次最多 100

    返回:
        一个列表，每个元素是一篇论文的字典，例如：
        [
            {
                "paperId": "abc123...",
                "title": "Remote Work and Productivity",
                "abstract": "We study ...",
                "year": 2023,
                "authors": ["Alice Smith", "Bob Lee"],   # 注意：已经压成字符串列表
                "citationCount": 42,
            },
            ...
        ]
        没有摘要(abstract)的论文会被过滤掉，因为后续切片 4 要靠摘要抽主张。
    """

    # 构造 URL 查询参数。注意：limit 多取一些，方便过滤掉没摘要的之后还能凑够数
    params = {
        "query": query,                # 搜索关键词
        "limit": min(limit * 2, 100),  # 多拉一倍，最多 100，对冲掉空摘要的损失
        "fields": PAPER_FIELDS,        # 告诉 SS 我们要哪些字段
    }

    # 用 httpx.AsyncClient 上下文管理器，自动管理连接池和关闭
    # timeout 同时控制连接超时和读取超时
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:

        # 重试循环：处理 429（限流）和瞬时网络错误
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # await 在这里释放事件循环，等 HTTP 响应回来
                response = await client.get(SEMANTIC_SCHOLAR_URL, params=params)

                # 命中限流：等一会儿再试。SS 的限流策略大约是每秒 1 次（无 key）
                if response.status_code == 429:
                    wait = 2 ** attempt  # 指数退避：2、4、8 秒
                    print(f"[警告] 遇到限流(429)，第 {attempt}/{MAX_RETRIES} 次重试，等待 {wait}s ...")
                    await asyncio.sleep(wait)
                    continue  # 跳到下一次循环重试

                # 其它非 2xx 错误：直接抛出，由外层捕获
                response.raise_for_status()

                # 解析 JSON。SS 的返回格式是 {"total": N, "data": [...]}
                payload = response.json()
                raw_papers = payload.get("data", [])
                break  # 拿到数据就跳出重试循环

            except httpx.TimeoutException:
                # 网络超时：通常是 SS 服务器慢或本地网络抖动
                print(f"[警告] 请求超时，第 {attempt}/{MAX_RETRIES} 次重试 ...")
                if attempt == MAX_RETRIES:
                    # 最后一次还失败，直接抛出
                    raise
                await asyncio.sleep(2)

            except httpx.HTTPStatusError as e:
                # 4xx/5xx 错误。401/403 一般不会出现（这个 API 不强制 key）
                # 5xx 是 SS 自己的问题，也重试一下
                if 500 <= e.response.status_code < 600 and attempt < MAX_RETRIES:
                    print(f"[警告] 服务器错误 {e.response.status_code}，重试中 ...")
                    await asyncio.sleep(2)
                    continue
                raise  # 其它情况直接抛出

        else:
            # 重试循环正常跑完都没 break = 全部失败
            raise RuntimeError(f"调用 Semantic Scholar 失败，已重试 {MAX_RETRIES} 次")

    # ===== 数据清洗 =====
    cleaned: list[dict] = []  # 存放过滤+整理后的论文
    for paper in raw_papers:
        # 关键过滤：没有摘要的论文直接跳过（后续抽主张要靠摘要）
        abstract: Optional[str] = paper.get("abstract")
        if not abstract:
            continue

        # authors 字段在 SS 返回里是 [{"authorId": "...", "name": "..."}, ...]
        # 我们只要名字，压成纯字符串列表，更简洁也方便存数据库
        authors_raw = paper.get("authors") or []
        author_names = [a.get("name", "") for a in authors_raw if a.get("name")]

        # 组装我们自己的标准格式
        cleaned.append({
            "paperId": paper.get("paperId"),
            "title": paper.get("title") or "",                  # 保险：避免 None
            "abstract": abstract,
            "year": paper.get("year"),                          # 可能是 None
            "authors": author_names,
            "citationCount": paper.get("citationCount") or 0,   # 没引用就是 0
        })

        # 凑够 limit 篇就停，多余的丢弃
        if len(cleaned) >= limit:
            break

    return cleaned


# ===== 文件直接运行时的测试入口 =====
# 这是 Python 的标准模式：当文件作为脚本直接跑时，__name__ == "__main__"
# 当作为模块被 import 时，下面的代码不会执行
if __name__ == "__main__":

    async def _demo():
        """演示用例：搜 'remote work productivity' 并美化打印结果。"""
        query = "remote work productivity"
        print(f"\n>>> 正在搜索：{query} ...\n")

        # await 调用我们写的异步函数
        papers = await search_papers(query, limit=5)

        print(f">>> 拿到 {len(papers)} 篇有摘要的论文：\n")
        for i, p in enumerate(papers, start=1):
            # 标题 + 年份 + 引用数 + 作者一行展示
            authors_str = ", ".join(p["authors"][:3])  # 只显示前 3 个作者
            if len(p["authors"]) > 3:
                authors_str += " et al."
            print(f"[{i}] {p['title']}")
            print(f"    年份: {p['year']}  |  引用: {p['citationCount']}  |  作者: {authors_str}")
            # 摘要太长，截断显示
            snippet = p["abstract"][:200].replace("\n", " ")
            print(f"    摘要: {snippet}...\n")

        print(">>> 完整结构示例（第一篇的全部字段）：")
        if papers:
            pprint(papers[0])

    # asyncio.run() 是启动协程的入口，它会创建事件循环、跑完、关闭
    asyncio.run(_demo())


# ===========================================================
# 如何运行这个文件
# ===========================================================
# 1. 先激活虚拟环境（每次新开终端都要做）：
#       Windows Git Bash:    source venv/Scripts/activate
#       Windows PowerShell:  venv\Scripts\Activate.ps1
#       macOS / Linux:       source venv/bin/activate
#
# 2. 在 backend 目录下运行：
#       python fetcher.py
#
# 3. 你应该看到 5 篇论文的标题、年份、作者、摘要片段，最后一篇的完整字段。
#
# 常见报错：
#   - ModuleNotFoundError: No module named 'httpx'
#       → venv 没激活，或者忘装依赖。重新 pip install httpx
#   - httpx.ConnectError
#       → 网络问题，可能要开代理才能访问 Semantic Scholar
#   - 一直卡在 429
#       → 你跑得太频繁了。等一分钟再试，或者去 SS 官网申请免费 API key
# ===========================================================
