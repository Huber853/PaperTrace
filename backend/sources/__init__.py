"""
PaperTrace - 数据源包入口
==========================

这个文件做两件事：
  1. 提供 get_source(name) 工厂函数 —— 根据名字拿对应的数据源实例
  2. 提供 search_with_fallback(query, limit) —— 主源失败时自动切换到备用源

----------------------------------------------------
新手讲解：什么是工厂模式？
----------------------------------------------------
想象你在写一个画图软件，要支持画 圆 / 方 / 三角形。
朴素写法：

    if shape_name == "circle":
        s = Circle()
    elif shape_name == "square":
        s = Square()
    elif shape_name == "triangle":
        s = Triangle()

每次新增形状都要改 N 处 if-else，丑且容易漏。

工厂模式：把"根据名字造对象"这件事封装到一个函数里：

    def get_shape(name):
        return {
            "circle": Circle,
            "square": Square,
            "triangle": Triangle,
        }[name]()

好处：
  - 调用方只关心"我要 circle"，不关心 Circle 怎么 new
  - 新增形状只改这一个函数，别的地方零改动
  - 名字打错时立刻 KeyError，比 if-else 漏一支安全

我们的 get_source("openalex") / get_source("arxiv") 就是这个套路。
"""

# ===== 导入区 =====
from __future__ import annotations

import logging
from typing import Type

from .base import BaseSource, Paper
from .openalex import OpenAlexSource
from .arxiv import ArxivSource

logger = logging.getLogger(__name__)


# ===== 注册表 =====
# 把所有可用的数据源类登记在这里。
# 注意：值是"类"本身（Type[BaseSource]），不是实例。get_source 时再 new。
# 这样设计的好处：实例的构造可能需要参数（比如 base_url），延迟到调用时再决定
_REGISTRY: dict[str, Type[BaseSource]] = {
    "openalex": OpenAlexSource,
    "arxiv": ArxivSource,
}


# ===== 工厂函数 =====
def get_source(name: str = "openalex", **kwargs) -> BaseSource:
    """
    根据名字拿对应的数据源实例。

    参数:
        name: 数据源名字。当前支持 "openalex" / "arxiv"
        **kwargs: 透传给数据源的构造函数（比如 base_url、timeout）

    返回:
        BaseSource 子类的实例

    抛出:
        ValueError: name 不在注册表里

    示例:
        >>> src = get_source("openalex")
        >>> papers = await src.search("remote work", limit=10)
        >>>
        >>> # 测试 fallback：用一个故意写错的 URL
        >>> bad = get_source("openalex", base_url="https://invalid.example.com/works")
    """
    name_lower = name.lower()
    if name_lower not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise ValueError(
            f"未知数据源 {name!r}，目前支持：{available}"
        )
    cls = _REGISTRY[name_lower]
    return cls(**kwargs)


# ===== 带 fallback 的搜索 =====
# 主源 → 备用源的顺序写在这里。未来加 PubMed 时往这个列表后面塞一个就行
DEFAULT_FALLBACK_CHAIN = ["openalex", "arxiv"]


async def search_with_fallback(
    query: str,
    limit: int,
    chain: list[str] = None,
    sources: list[BaseSource] = None,
) -> list[Paper]:
    """
    依次尝试多个数据源，直到拿到非空结果。

    参数:
        query, limit: 透传给各个数据源的 search()
        chain: 数据源名字的尝试顺序，默认 ["openalex", "arxiv"]
        sources: 直接传入构造好的实例，覆盖 chain（用于测试）

    返回:
        第一个成功且非空的数据源返回的 list[Paper]。
        如果所有源都失败或都返回空列表，返回最后一次的结果（可能是空列表）。

    降级触发条件:
        - 当前源抛出任何异常（限流、网络、解析错）→ 切下一个
        - 当前源返回 0 篇 → 也切下一个（说明源里没收录这个话题）

    示例:
        # 默认链
        papers = await search_with_fallback("remote work", limit=10)

        # 测试 fallback：故意把 OpenAlex 的 base_url 改坏
        from sources import OpenAlexSource, ArxivSource
        broken = OpenAlexSource(base_url="https://invalid.example.com/works")
        backup = ArxivSource()
        papers = await search_with_fallback("remote work", 10, sources=[broken, backup])
    """
    # 决定要试的源列表
    if sources is None:
        chain = chain or DEFAULT_FALLBACK_CHAIN
        sources = [get_source(name) for name in chain]

    last_result: list[Paper] = []
    for src in sources:
        try:
            logger.info("[fallback] 尝试数据源：%s", src.name)
            papers = await src.search(query, limit)
        except Exception as e:
            # 任何异常都视为本源失败，记录后试下一个。
            # 注意：用 exception=False 的 warning，不打全 stack trace，
            # 否则日志会被淹没。真正想 debug 时改成 logger.exception 即可。
            logger.warning("[fallback] %s 失败：%s（切换下一个源）", src.name, e)
            continue

        if papers:
            logger.info("[fallback] %s 成功拿到 %d 篇", src.name, len(papers))
            return papers

        # 没异常但 0 篇 —— 也算"这个源不行"，继续试
        logger.warning("[fallback] %s 返回 0 篇（切换下一个源）", src.name)
        last_result = papers

    logger.error("[fallback] 所有数据源都没拿到结果")
    return last_result


# ===== 公开导出 =====
# 让外部 from sources import Paper / OpenAlexSource 等也能直接拿到
__all__ = [
    "Paper",
    "BaseSource",
    "OpenAlexSource",
    "ArxivSource",
    "get_source",
    "search_with_fallback",
    "DEFAULT_FALLBACK_CHAIN",
]


# ===== 直接运行时的自检 =====
if __name__ == "__main__":
    import asyncio
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("=" * 50)
    print("[1/3] 测试 get_source 工厂")
    print("=" * 50)
    s1 = get_source("openalex")
    assert isinstance(s1, OpenAlexSource)
    s2 = get_source("arxiv")
    assert isinstance(s2, ArxivSource)
    try:
        get_source("pubmed")
    except ValueError as e:
        print(f"✓ 未知名字正确抛 ValueError：{e}")
    print("✓ get_source 工厂正确")

    print()
    print("=" * 50)
    print("[2/3] 默认 fallback 链（OpenAlex → arXiv）")
    print("=" * 50)

    async def _demo_default():
        papers = await search_with_fallback("remote work productivity", limit=5)
        print(f"\n>>> 拿到 {len(papers)} 篇论文")
        for i, p in enumerate(papers, start=1):
            print(f"[{i}] ({p.source_id}) {p.title[:80]}")

    asyncio.run(_demo_default())

    print()
    print("=" * 50)
    print("[3/3] 模拟主源失败 → 切到 arXiv")
    print("=" * 50)

    async def _demo_fallback():
        # 把 OpenAlex 的 URL 写坏，强制走 fallback
        broken = OpenAlexSource(base_url="https://invalid.example.com/works")
        backup = ArxivSource()
        papers = await search_with_fallback(
            "remote work productivity",
            limit=5,
            sources=[broken, backup],
        )
        print(f"\n>>> 拿到 {len(papers)} 篇论文（应该来自 arXiv）")
        for i, p in enumerate(papers, start=1):
            print(f"[{i}] ({p.source_id}) {p.title[:80]}")

    asyncio.run(_demo_fallback())
