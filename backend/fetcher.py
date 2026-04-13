"""
PaperTrace - 论文数据获取入口（已迁移到多数据源）
==================================================

历史背景：
  - 切片 2 时这里直接调 Semantic Scholar
  - 后来 SS 在国内访问限流非常严重，遇到 429 几乎不可用
  - 于是抽出 backend/sources/ 子包，做成"多数据源 + 自动 fallback"
  - 这个文件现在变成一层薄薄的"适配器 + 缓存"，把 sources 的结果包成
    main.py 已经在用的旧 dict 格式（避免 main.py 也要改）

----------------------------------------------------
对外接口（保持不变）
----------------------------------------------------
async def search_papers(query, limit=20) -> list[dict]
返回的 dict 字段：
    paperId        论文唯一 ID（OpenAlex 的 W 号 / arXiv 号 / ...）
    title          标题
    abstract       摘要正文
    year           年份
    authors        作者名字列表
    citationCount  引用数

新增字段（main.py 暂时没用，但保留方便未来扩展）：
    source         数据真正来自哪个源（"openalex" / "arxiv"）
    doi            DOI（可能为 None）
    url            论文页面 URL（可能为 None）

----------------------------------------------------
为什么不让 main.py 直接用 Paper Pydantic 模型？
----------------------------------------------------
那样改动面更大（main.py / database.py / 前端类型都要联动）。
当前作法是"兼容层"：
  sources 返回结构化的 Paper（内部清晰）
  fetcher 翻译成旧 dict（外部稳定）
  未来想升级 main.py 时，只需要让它直接 import sources 即可，
  fetcher 这一层就可以删掉。
"""

# ===== 导入区 =====
from __future__ import annotations

import sys                              # Windows 控制台编码修正
from pprint import pprint               # 调试美化打印

# 缓存：fetcher 这一层做"对外的最终结果"缓存，避免每次都跑一遍 fallback
from cache import get_cache, set_cache

# 数据源子包
from sources import Paper, search_with_fallback

# Windows 控制台默认是 GBK，会让中文 print 变乱码。强制 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


# ===== 缓存命名空间 =====
# 注意：故意改名了。
#   - 旧值 "ss_search" 缓存的是 Semantic Scholar 的格式
#   - 新值 "papers_search" 缓存的是新 dict 格式（含 source / doi / url）
# 改名能让旧的 ss_search_*.json 文件自然失效，不会被错误地反序列化
CACHE_PREFIX = "papers_search"


# ===== 内部工具：Paper → 旧 dict 格式 =====
def _paper_to_legacy_dict(paper: Paper, source_name: str) -> dict:
    """
    把统一的 Paper 模型翻译成 main.py 期望的旧 dict 形状。

    旧字段名沿用 Semantic Scholar 时代的 camelCase（paperId / citationCount），
    避免改动 main.py。新字段（source / doi / url）顺手带上，将来可能用得到。
    """
    return {
        # ----- 旧字段（main.py 在用，保持稳定）-----
        "paperId": paper.source_id,
        "title": paper.title,
        "abstract": paper.abstract,
        "year": paper.year,
        "authors": paper.authors,
        "citationCount": paper.citation_count,
        # ----- 新字段（保留以便扩展）-----
        "source": source_name,
        "doi": paper.doi,
        "url": paper.url,
    }


# ===== 核心函数 =====
async def search_papers(
    query: str,
    limit: int = 20,
    refresh: bool = False,
) -> tuple[list[dict], str]:
    """
    异步搜索论文。

    流程：
        1. 先查本地缓存（命中且未过期则直接返回）
        2. 走 sources.search_with_fallback：
             OpenAlex → 失败/0 篇 → arXiv
        3. 把统一 Paper 翻译成旧 dict 格式
        4. 写缓存
        5. 返回

    参数:
        query:   搜索关键词，例如 "remote work productivity"
        limit:   最多返回多少篇
        refresh: True 时跳过缓存强制重新请求外部 API（用于"刷新数据"按钮）

    返回:
        (papers, fetched_at)
            papers      — list[dict]，字段格式见模块顶部说明
            fetched_at  — ISO 8601 时间戳（UTC），表示这批数据"最初是什么时候
                          从外部源获取的"。缓存命中时是当初写入缓存的时间，
                          缓存未命中或 refresh=True 时是"现在"。
    """

    # ===== 第一步：缓存键 =====
    # 注意：缓存键里**不包含**数据源名字。
    # 想想为什么 —— 我们关心的是"对同一个 query+limit 是否给出过结果"，
    # 至于结果是 OpenAlex 还是 arXiv 给的，对调用方来说无所谓。
    # 这样一旦缓存命中，无论原本走的是主源还是备用源，都能省掉网络往返。
    cache_key = {"query": query, "limit": limit, "v": 2}
    # "v": 2 是缓存版本号；如果未来字段格式又变了，把它加 1 就能让旧缓存自然失效

    # ===== 第二步：查缓存（除非 refresh=True） =====
    # refresh=True 是用户在前端点了"刷新数据"按钮，希望强制走一次真请求。
    # 这种情况下我们直接跳过 get_cache，避免命中老结果。
    if not refresh:
        cached = get_cache(CACHE_PREFIX, cache_key)
        if cached is not None:
            data, cached_at = cached
            print(
                f"[缓存命中] query={query!r} limit={limit} "
                f"→ 直接返回 {len(data)} 篇（cached_at={cached_at}）"
            )
            return data, cached_at
    else:
        print(f"[强制刷新] query={query!r} limit={limit} → 跳过缓存")

    # ===== 第三步：走多源 fallback =====
    # search_with_fallback 内部会按 [openalex, arxiv] 的顺序尝试，
    # 任何一个源抛异常或返回 0 篇都会切下一个。
    # 我们这一层不需要写任何 try/except —— 让所有源都失败的极端情况自然抛出来，
    # 由 main.py 的后台任务捕获并把任务标 failed。
    papers: list[Paper] = await search_with_fallback(query, limit=limit)

    # ===== 第四步：翻译成旧 dict 格式 =====
    # 用 paper.url 反推数据源名字略奇怪，更直接的做法是让 search_with_fallback
    # 也告诉我们用了哪个源。但当前需求只用得到名字标识一下，简单做法：
    # 所有 Paper 都没显式带 source 字段，那我们用一个启发式 —— 看 URL 里有没有 arxiv.org
    cleaned: list[dict] = []
    for p in papers:
        source_name = "arxiv" if (p.url and "arxiv.org" in p.url) else "openalex"
        cleaned.append(_paper_to_legacy_dict(p, source_name))

    # ===== 第五步：写缓存 =====
    # 即使 cleaned 是空列表也写入 —— 否则同一个空查询会被反复打两次外部 API。
    # set_cache 现在会返回写入时的 cached_at，正好作为本次的 fetched_at 返回给上层。
    fetched_at = set_cache(CACHE_PREFIX, cache_key, cleaned)

    return cleaned, fetched_at


# ===== 文件直接运行时的测试入口 =====
if __name__ == "__main__":
    import asyncio
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def _demo():
        """演示用例：搜 'remote work productivity' 并美化打印结果。"""
        query = "remote work productivity"
        print(f"\n>>> 正在搜索：{query} ...\n")

        papers, fetched_at = await search_papers(query, limit=5)

        print(f"\n>>> 拿到 {len(papers)} 篇有摘要的论文（fetched_at={fetched_at}）：\n")
        for i, p in enumerate(papers, start=1):
            authors_str = ", ".join(p["authors"][:3])
            if len(p["authors"]) > 3:
                authors_str += " et al."
            print(f"[{i}] {p['title']}")
            print(
                f"    来源: {p['source']}  |  ID: {p['paperId']}  |  "
                f"年份: {p['year']}  |  引用: {p['citationCount']}"
            )
            print(f"    作者: {authors_str}")
            snippet = p["abstract"][:200].replace("\n", " ")
            print(f"    摘要: {snippet}...\n")

        print(">>> 完整结构示例（第一篇的全部字段）：")
        if papers:
            pprint(papers[0])

    asyncio.run(_demo())


# ===========================================================
# 如何运行
# ===========================================================
# 1. 激活虚拟环境：
#       source venv/Scripts/activate
#
# 2. 在 backend 目录下运行：
#       PYTHONIOENCODING=utf-8 python fetcher.py
#
# 3. 你应该看到 5 篇论文（多半来自 OpenAlex）。
#
# 测试 fallback（OpenAlex 故障 → 切到 arXiv）：
#   见 sources/__init__.py 的自检 [3/3]，或这样写一个一次性脚本：
#
#       from sources import OpenAlexSource, ArxivSource, search_with_fallback
#       broken = OpenAlexSource(base_url="https://invalid.example.com/works")
#       backup = ArxivSource()
#       papers = await search_with_fallback("remote work", 5, sources=[broken, backup])
#       # 应该能拿到 arXiv 的结果
#
# 常见报错：
#   - ModuleNotFoundError: No module named 'feedparser'
#       → pip install feedparser
#   - httpx.ConnectError
#       → 网络问题。OpenAlex 国内一般通；arXiv 偶尔需要代理
#   - 两个源都返回 0 篇
#       → query 太冷门或有奇怪字符；换个关键词试试
# ===========================================================
