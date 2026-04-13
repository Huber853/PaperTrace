"""
PaperTrace - OpenAlex 数据源
============================

OpenAlex（https://openalex.org）是一个完全免费、对研究者友好的学术索引：
  - 收录 2.5 亿+ 论文，质量和 Semantic Scholar 相当
  - 没有强制 API key，只要在请求里加上 mailto 标识就能进入"polite pool"，限流配额高得多
  - 全球 CDN 加速，国内访问也比 Semantic Scholar 稳得多 → 这就是我们迁过来的理由

文档：https://docs.openalex.org/

----------------------------------------------------
新手讲解：什么是 polite pool ？
----------------------------------------------------
OpenAlex 把请求分成两个池子：
  - common pool：默认池，限流严格，会被很多人挤
  - polite pool：你告诉 OpenAlex 你是谁（写个邮箱），就能进
                 限流松得多，速度快，且更稳定

加入 polite pool 的方法（任选其一或都用）：
  1. 在 User-Agent 里加 "mailto:you@example.com"
  2. 在 query string 里加 "mailto=you@example.com"

我们在 backend/.env 里读 OPENALEX_EMAIL，没配的话用一个占位符也能跑通，
只是会被分到 common pool 而已。

----------------------------------------------------
新手讲解：abstract_inverted_index 是个什么鬼？
----------------------------------------------------
出于版权原因，OpenAlex 不直接给你摘要的连续文本，
而是给一份"倒排索引"：

    {
        "We": [0],
        "study": [1],
        "remote": [2, 7],
        "work": [3, 8],
        ...
    }

意思是：第 0 个词是 "We"、第 1 个词是 "study"、第 2 和第 7 个词都是 "remote" ...
还原成正常文本就是 "We study remote work ... remote work ..."。

reconstruct_abstract() 函数就是干这个还原的。
"""

# ===== 导入区 =====
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

# 注意：父级目录的 cache 和 http_client 模块。我们用相对/绝对哪种 import 都行。
# 这里用绝对 import，方便理解：sources 包里的代码也可以直接 import backend 顶层的工具
from http_client import request_with_retry
from .base import BaseSource, Paper

logger = logging.getLogger(__name__)


# ===== 工具函数：还原 OpenAlex 倒排索引 =====
def reconstruct_abstract(inverted: Optional[dict]) -> str:
    """
    把 OpenAlex 的 abstract_inverted_index 还原成连续的摘要文本。

    参数:
        inverted: 形如 {"word": [pos1, pos2, ...]} 的 dict；可能为 None

    返回:
        还原后的摘要字符串。空输入返回空串。

    算法:
        1. 倒过来建一个 {position: word} 的 dict
        2. 找到最大 position，从 0 数到它，按位置取词，没有的填空串
        3. 用空格 join 起来

    边界:
        - 输入 None / 空 dict → 返回 ""
        - 中间有缺位（理论上不会，但出于鲁棒性考虑）→ 缺的位置用空串，
          最后用 " ".join 之后会留一个连续的空格，对 LLM 抽取无影响

    示例:
        >>> reconstruct_abstract({"We": [0], "study": [1], "X": [2]})
        'We study X'
        >>> reconstruct_abstract({"hi": [0, 2], "world": [1]})
        'hi world hi'
        >>> reconstruct_abstract(None)
        ''
    """
    if not inverted:
        return ""

    # 倒过来建 position → word 的映射
    positions: dict[int, str] = {}
    for word, pos_list in inverted.items():
        for pos in pos_list:
            positions[pos] = word

    if not positions:
        return ""

    # 按位置 0..max 顺序取词。缺位用空串。
    max_pos = max(positions)
    return " ".join(positions.get(i, "") for i in range(max_pos + 1))


# ===== OpenAlex 数据源实现 =====
class OpenAlexSource(BaseSource):
    """
    OpenAlex 数据源。

    用法：
        src = OpenAlexSource()
        papers = await src.search("remote work productivity", limit=20)

    可配置：
        构造时传 base_url 可以覆盖默认 URL，方便测试 fallback：
            OpenAlexSource(base_url="https://invalid.example.com/works")
    """

    name = "openalex"

    # OpenAlex 文档约定的字段集；select 参数能让响应小一些，只返回我们要的字段
    SELECT_FIELDS = (
        "id,title,abstract_inverted_index,publication_year,"
        "authorships,cited_by_count,doi"
    )

    def __init__(
        self,
        base_url: str = "https://api.openalex.org/works",
        timeout: float = 30.0,
    ):
        # 注意：把 base_url 做成构造参数，是为了能在测试里临时换成坏 URL
        # 来验证 fallback 机制是否真的会切到 arXiv
        self.base_url = base_url
        self.timeout = timeout

    async def search(self, query: str, limit: int) -> list[Paper]:
        # ===== 准备请求参数 =====
        # OpenAlex 的搜索参数：
        #   search       全文检索关键词
        #   per-page     单页返回数量（最大 200）
        #   select       指定要返回的字段，省带宽
        # 多取一倍是为了过滤掉没有摘要的论文之后还能凑够数
        params: dict[str, object] = {
            "search": query,
            "per-page": min(limit * 2, 200),
            "select": self.SELECT_FIELDS,
        }

        # ===== Polite pool 标识 =====
        # 读环境变量 OPENALEX_EMAIL；没配就用一个占位符（仍然合法，只是没礼貌分）
        email = os.getenv("OPENALEX_EMAIL", "anonymous@papertrace.local")
        params["mailto"] = email
        # 双保险：同时在 User-Agent 里也写一份。OpenAlex 文档里两种都接受
        headers = {
            "User-Agent": f"PaperTrace/1.0 (mailto:{email})",
            "Accept": "application/json",
        }

        # ===== 发请求（带 429 退避）=====
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await request_with_retry(
                client,
                "GET",
                self.base_url,
                params=params,
                headers=headers,
            )

        payload = response.json()
        # OpenAlex 的返回结构是 {"meta": {...}, "results": [...]}
        raw_works = payload.get("results", [])
        logger.info("OpenAlex 返回 %d 条原始结果", len(raw_works))

        # ===== 转成统一 Paper =====
        papers: list[Paper] = []
        for work in raw_works:
            # 摘要还原。没摘要的直接跳过
            abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
            if not abstract.strip():
                continue

            # OpenAlex 的 id 是完整 URL，例如 "https://openalex.org/W2741809807"
            # 我们存最后那一段，更短更好看
            full_id = work.get("id") or ""
            source_id = full_id.rsplit("/", 1)[-1] if full_id else ""

            # authorships 是 [{"author": {"display_name": "..."}}, ...]
            author_names = [
                a.get("author", {}).get("display_name", "")
                for a in (work.get("authorships") or [])
                if a.get("author", {}).get("display_name")
            ]

            # OpenAlex 的 doi 字段已经是完整 URL（"https://doi.org/10.xxxx"）
            # 我们存原值即可
            doi = work.get("doi")

            papers.append(
                Paper(
                    source_id=source_id,
                    title=work.get("title") or "",
                    abstract=abstract,
                    year=work.get("publication_year"),
                    authors=author_names,
                    citation_count=work.get("cited_by_count") or 0,
                    doi=doi,
                    # OpenAlex 的 id 本身就是论文页面 URL，直接用
                    url=full_id or None,
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

    # 单元测试：先验证 reconstruct_abstract 这个纯函数
    print("=" * 50)
    print("[1/2] 测试 reconstruct_abstract")
    print("=" * 50)
    assert reconstruct_abstract(None) == ""
    assert reconstruct_abstract({}) == ""
    assert reconstruct_abstract({"hi": [0]}) == "hi"
    assert reconstruct_abstract({"hi": [0, 2], "world": [1]}) == "hi world hi"
    assert reconstruct_abstract({"We": [0], "study": [1], "X": [2]}) == "We study X"
    print("✓ reconstruct_abstract 5 条断言全过")

    # 集成测试：真正打 OpenAlex
    print()
    print("=" * 50)
    print("[2/2] 真实调用 OpenAlex API")
    print("=" * 50)

    async def _demo():
        src = OpenAlexSource()
        papers = await src.search("remote work productivity", limit=5)
        print(f"\n>>> 拿到 {len(papers)} 篇论文：\n")
        for i, p in enumerate(papers, start=1):
            authors_str = ", ".join(p.authors[:3])
            if len(p.authors) > 3:
                authors_str += " et al."
            print(f"[{i}] {p.title}")
            print(f"    年份: {p.year}  |  引用: {p.citation_count}  |  作者: {authors_str}")
            print(f"    DOI: {p.doi}")
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
#   PYTHONIOENCODING=utf-8 python -m sources.openalex
#
# 注意：必须用 -m 模块方式跑，因为这个文件 import 了同包的 .base
# 直接 python sources/openalex.py 会报 ImportError。
# ===========================================================
