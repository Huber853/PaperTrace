"""
PaperTrace - 数据源抽象层（base）
==================================

这个文件定义两样东西：
  1. Paper —— 一篇论文的统一格式（用 Pydantic 模型表达）
  2. BaseSource —— 所有数据源都要实现的抽象基类（接口）

----------------------------------------------------
新手讲解一：什么是 Pydantic？
----------------------------------------------------
你可能写过这种代码：

    paper = {"title": "...", "year": 2024, "authors": [...]}

问题是：
  - "year" 万一传成字符串 "2024" 你不会立刻发现
  - "authors" 万一忘了传，后续代码会炸但定位很慢
  - 别人接手代码不知道这个 dict 应该有哪些字段

Pydantic 解决这些痛点：你写一个 class，列出每个字段的名字和类型，
Pydantic 自动帮你做：
  - 类型检查（传错类型立即报错）
  - 自动转换（"2024" 字符串能自动变 int 2024）
  - JSON 序列化/反序列化
  - IDE 自动补全（敲 paper. 会列出所有字段）

可以把 Pydantic 类理解成"带类型校验的高级 dict"。

----------------------------------------------------
新手讲解二：什么是抽象基类（ABC）？
----------------------------------------------------
"抽象基类"翻译自 Abstract Base Class，简称 ABC。

想象你在设计一套快递系统，要支持顺丰、京东、菜鸟三种快递。
它们都要能"下单"和"查询物流"，但每家的具体实现完全不一样。

你可以这样写：

    class BaseDelivery(ABC):
        @abstractmethod
        def create_order(self, address): ...
        @abstractmethod
        def query_status(self, order_id): ...

    class SFDelivery(BaseDelivery):
        def create_order(self, address): ...
        def query_status(self, order_id): ...

好处：
  - BaseDelivery 是一份"合同"，规定子类必须实现哪些方法
  - 子类如果忘了实现某个方法，Python 在你 new 它的时候就直接报错
  - 上层代码只依赖 BaseDelivery，不关心是顺丰还是京东 → 换一家快递只改一行

我们的 BaseSource 就是这样的合同：所有数据源都必须实现 search()。
"""

# ===== 导入区 =====
from __future__ import annotations

from abc import ABC, abstractmethod  # ABC + @abstractmethod 是定义抽象基类的标准做法
from typing import Optional

from pydantic import BaseModel, Field


# ===== 统一论文模型 =====
class Paper(BaseModel):
    """
    一篇论文的统一格式。

    所有数据源（OpenAlex / arXiv / 未来可能加的 PubMed 等）都返回这个类型，
    上层代码完全不用关心数据是从哪儿来的。

    字段说明：
        source_id     该数据源里的论文唯一 ID
                      OpenAlex: "W2741809807"
                      arXiv:    "2106.12345"
        title         论文标题
        abstract      摘要正文（必须非空，没摘要的论文应该被数据源直接过滤掉）
        year          发表年份，可能为 None（数据源没给）
        authors       作者名字列表，按顺序
        citation_count 被引次数。arXiv 不提供，统一填 0
        doi           DOI 标识符（可选），例如 "10.1038/s41586-021-03534-y"
        url           论文页面或 PDF 的 URL（可选）

    Pydantic 用法示例：
        >>> p = Paper(source_id="W123", title="...", abstract="...", year=2024)
        >>> p.year
        2024
        >>> p.model_dump()              # 转成普通 dict
        {'source_id': 'W123', 'title': '...', ...}
        >>> Paper.model_validate(d)     # 从 dict 反向构造
    """

    source_id: str
    title: str
    abstract: str  # 必填：没摘要的论文不应该被造出来
    year: Optional[int] = None
    authors: list[str] = Field(default_factory=list)
    citation_count: int = 0
    doi: Optional[str] = None
    url: Optional[str] = None


# ===== 数据源接口 =====
class BaseSource(ABC):
    """
    所有数据源的抽象基类。

    要新增一个数据源（比如未来加 PubMed），你只需要：
        class PubMedSource(BaseSource):
            name = "pubmed"
            async def search(self, query, limit):
                ...返回 list[Paper]...

    然后注册到 sources/__init__.py 的 get_source() 工厂函数里就完事了。
    上层代码（fetcher / main）一行都不用改。
    """

    # 子类必须给一个简短的名字，用作工厂函数的 key 和日志标识
    name: str = "base"

    @abstractmethod
    async def search(self, query: str, limit: int) -> list[Paper]:
        """
        搜索论文。

        参数:
            query: 用户输入的研究问题，自然语言关键词
            limit: 期望返回的论文数量上限。子类可以返回少于这个数（比如限流后只拿到一部分）

        返回:
            一个 Paper 列表，已经过滤掉没有摘要的条目。

        实现要求:
            - 必须是 async 函数（用 await 调 httpx 或别的网络库）
            - 没摘要的论文不要返回（Paper 模型 abstract 字段必填，给空串都不行）
            - 网络错误不要静默，让异常往上抛，由 search_with_fallback 决定降级策略
            - len(返回值) 可以小于 limit，但不能大于 limit
        """
        # 这里不写实现，留给子类。abstractmethod 装饰器会强制子类必须 override
        # 如果子类忘了实现，Python 在 PubMedSource() 实例化时就立刻 TypeError
        raise NotImplementedError
