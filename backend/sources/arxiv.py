"""
PaperTrace - arXiv 数据源（备用源）
====================================

arXiv（https://arxiv.org）是物理、数学、CS、定量金融等领域的预印本仓库。
对 PaperTrace 来说它的优势是：
  - 完全公开，零限流，永远不会 429
  - 国内访问稳定（有镜像，且本身没有 CDN 反爬）
  - 接口简单：纯 HTTP GET，返回 Atom XML

劣势：
  - 只覆盖部分学科，社科/医学等领域结果会少
  - 不提供 citation count，统一填 0
  - 单页最多约 2000 条，但实际响应慢，建议 limit ≤ 50

文档：https://info.arxiv.org/help/api/user-manual.html

----------------------------------------------------
为什么要专门做"备用源"？
----------------------------------------------------
工程上叫"fallback"。当主源（OpenAlex）出问题时（限流、宕机、网络不通），
我们不希望整个产品挂掉，而是自动切到备用源继续工作。
对用户来说，结果质量可能稍降，但流程不中断 → 体验远好于直接报错。

----------------------------------------------------
为什么用 feedparser 而不是手写 XML 解析？
----------------------------------------------------
arXiv 返回的是 Atom 1.0 协议的 XML（一种 RSS 类格式）。
手写 XML 解析既容易写错（命名空间、CDATA、转义……）又费时间。
feedparser 是 Python 生态里最成熟的 RSS/Atom 解析库，
直接 entry.title / entry.summary / entry.authors 就能拿到字段。

依赖安装：
    pip install feedparser
"""

# ===== 导入区 =====
from __future__ import annotations

import logging
from typing import Optional

import feedparser  # 第三方库，pip install feedparser
import httpx

from http_client import request_with_retry
from .base import BaseSource, Paper

logger = logging.getLogger(__name__)


# ===== 工具函数 =====
def _parse_year(published: Optional[str]) -> Optional[int]:
    """
    arXiv 给的发布时间形如 "2024-03-15T10:00:00Z"，我们只要年份。
    解析失败返回 None，不抛异常。
    """
    if not published:
        return None
    try:
        return int(published[:4])
    except (ValueError, TypeError):
        return None


def _extract_arxiv_id(entry_id: str) -> str:
    """
    feedparser 解析出来的 entry.id 形如 "http://arxiv.org/abs/2106.12345v2"
    我们想要 "2106.12345v2"（带版本号），方便去重和引用。
    """
    if not entry_id:
        return ""
    return entry_id.rsplit("/", 1)[-1]


# ===== arXiv 数据源实现 =====
class ArxivSource(BaseSource):
    """
    arXiv 数据源。

    用法：
        src = ArxivSource()
        papers = await src.search("remote work productivity", limit=10)
    """

    name = "arxiv"

    def __init__(
        self,
        # arXiv 在 2024 年起强制 HTTPS：访问 http://export.arxiv.org/api/query
        # 会被 301 永久重定向到 https://，httpx 默认不跟随重定向会直接抛错。
        # 所以这里直接默认 https，省一次 redirect 还能避免误报。
        base_url: str = "https://export.arxiv.org/api/query",
        timeout: float = 30.0,
    ):
        # 同样把 base_url 暴露成构造参数，便于测试
        self.base_url = base_url
        self.timeout = timeout

    async def search(self, query: str, limit: int) -> list[Paper]:
        # ===== 请求参数 =====
        # arXiv 的 search_query 语法：
        #   all:keyword1+keyword2  → 全字段检索（"+" 号连接 = AND）
        #   ti:title               → 只搜标题
        #   au:author              → 只搜作者
        # 这里用 all: 因为我们不知道用户想搜哪个字段
        params = {
            "search_query": f"all:{query}",
            "max_results": min(limit * 2, 100),  # 同样多取一倍对冲空摘要
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

        # ===== 发请求 =====
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await request_with_retry(
                client,
                "GET",
                self.base_url,
                params=params,
            )

        # arXiv 返回的是 Atom XML，feedparser 既能吃 URL 也能吃字符串
        # 我们直接喂 response.text
        feed = feedparser.parse(response.text)

        # bozo=1 表示 feedparser 觉得 XML 有问题，但通常仍能解析
        # 我们只在 entries 真的为空时才警告
        if not feed.entries:
            logger.warning(
                "arXiv 返回 0 条结果（bozo=%s, status=%s）",
                getattr(feed, "bozo", "?"),
                response.status_code,
            )
            return []

        logger.info("arXiv 返回 %d 条原始结果", len(feed.entries))

        # ===== 转成统一 Paper =====
        papers: list[Paper] = []
        for entry in feed.entries:
            # entry.summary 就是摘要；arXiv 一般都有摘要，但保险起见过滤一下
            abstract = (entry.get("summary") or "").strip()
            if not abstract:
                continue

            # entry.authors 是 [{"name": "..."}, ...]（feedparser 标准化过）
            author_names = [
                a.get("name", "")
                for a in (entry.get("authors") or [])
                if a.get("name")
            ]

            # arXiv 的 DOI 可能在 entry["arxiv_doi"]，也可能没有
            doi = entry.get("arxiv_doi")

            # 论文页面 URL：entry.id 本身就是 abs/xxxxx 页面
            url = entry.get("id")

            papers.append(
                Paper(
                    source_id=_extract_arxiv_id(entry.get("id", "")),
                    title=(entry.get("title") or "").strip().replace("\n", " "),
                    abstract=abstract,
                    year=_parse_year(entry.get("published")),
                    authors=author_names,
                    citation_count=0,  # arXiv 不提供
                    doi=doi,
                    url=url,
                )
            )

            if len(papers) >= limit:
                break

        return papers


# ===== 直接运行时的自检 =====
if __name__ == "__main__":
    import asyncio
    import sys
    from pprint import pprint

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    # 1. 纯函数测试
    print("=" * 50)
    print("[1/2] 测试辅助函数")
    print("=" * 50)
    assert _parse_year(None) is None
    assert _parse_year("2024-03-15T10:00:00Z") == 2024
    assert _parse_year("garbage") is None
    assert _extract_arxiv_id("http://arxiv.org/abs/2106.12345v2") == "2106.12345v2"
    assert _extract_arxiv_id("") == ""
    print("✓ 辅助函数 5 条断言全过")

    # 2. 真实调用
    print()
    print("=" * 50)
    print("[2/2] 真实调用 arXiv API")
    print("=" * 50)

    async def _demo():
        src = ArxivSource()
        papers = await src.search("remote work productivity", limit=5)
        print(f"\n>>> 拿到 {len(papers)} 篇论文：\n")
        for i, p in enumerate(papers, start=1):
            authors_str = ", ".join(p.authors[:3])
            if len(p.authors) > 3:
                authors_str += " et al."
            print(f"[{i}] {p.title}")
            print(f"    arXiv ID: {p.source_id}  |  年份: {p.year}  |  作者: {authors_str}")
            snippet = p.abstract[:200].replace("\n", " ")
            print(f"    摘要: {snippet}...\n")
        if papers:
            print(">>> 第一篇的完整 Pydantic dump：")
            pprint(papers[0].model_dump())

    asyncio.run(_demo())


# ===========================================================
# 如何运行
# ===========================================================
#   cd backend
#   source venv/Scripts/activate
#   PYTHONIOENCODING=utf-8 python -m sources.arxiv
#
# 常见错误：
#   ModuleNotFoundError: No module named 'feedparser'
#       → 没装依赖。pip install feedparser
#   解析出来 0 条
#       → 关键词太冷门，arXiv 没收录；换个关键词或回退到 OpenAlex
# ===========================================================
