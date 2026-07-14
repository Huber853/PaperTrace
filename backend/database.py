"""
PaperTrace - 切片 3：数据库设计与存储
=====================================

作用：定义 papers / claims / contradictions 三张表，并提供初始化和会话工具。
被谁调用：
  - 切片 2 的 fetcher 拿到论文后 → 写入 papers 表
  - 切片 4 的 extractor 抽完主张后 → 写入 claims 表
  - 切片 5 的 contradiction 判完关系后 → 写入 contradictions 表
  - 切片 6 的 FastAPI 路由 → 读取并返回给前端

----------------------------------------------------
新手必读：什么是 ORM ？什么是外键？
----------------------------------------------------
1) ORM = Object-Relational Mapping（对象-关系映射）
   原本操作数据库要写 SQL：
       INSERT INTO papers (title, year) VALUES ('xxx', 2024);
       SELECT * FROM papers WHERE year > 2020;

   有了 ORM，你可以这样写 Python：
       paper = Paper(title="xxx", year=2024)
       session.add(paper)
       session.query(Paper).filter(Paper.year > 2020).all()

   ORM 帮你把 Python 对象 ↔ 数据库表行 自动来回翻译。
   好处：少写 SQL、不容易 SQL 注入、换数据库不用改代码（SQLite → MySQL → Postgres 都行）。

2) 外键（Foreign Key）
   外键就是"我这一行的某个字段，指向另一张表的某一行的主键"。
   作用：把两张表关联起来。

   例：claims 表的每一行 claim 都属于某一篇 paper。
   我们在 claims 表加一个 paper_id 字段，值等于 papers 表里某一行的 id。
   数据库会自动检查：你不能给 claim 写一个不存在的 paper_id。

   通过外键，我们能很容易地做"查一篇论文的所有主张"这种关联查询。

----------------------------------------------------
SQLAlchemy 2.0 新语法（DeclarativeBase）
----------------------------------------------------
旧的 1.x 语法是 declarative_base() 函数 + Column(...)。
2.0 推荐用 class DeclarativeBase + Mapped[xx] + mapped_column(...)，
好处是有完整的类型提示，IDE 能自动补全字段名。
"""

# ===== 导入区 =====
from datetime import datetime, timezone           # 时间戳：created_at 字段用
from pathlib import Path                          # 处理数据库文件路径，跨平台
from typing import Optional                       # 类型提示：可空字段

from sqlalchemy import (
    create_engine,                                # 创建数据库引擎（连接的入口）
    String, Integer, Float, Text, DateTime,       # 列的类型
    ForeignKey,                                   # 外键约束
    UniqueConstraint,                             # 唯一约束
)
from sqlalchemy.orm import (
    DeclarativeBase,                              # 2.0 的模型基类
    Mapped, mapped_column,                        # 2.0 的字段声明语法
    relationship,                                 # 表间关系（让对象能 .papers / .claims 访问）
    Session, sessionmaker,                        # 会话：所有读写都在 session 里完成
)


# ===== 数据库文件位置 =====
# 用 Path 拼出 backend/papertrace.db 的绝对路径，避免"在哪个目录运行就在哪建库"的混乱
BASE_DIR = Path(__file__).resolve().parent       # 当前 database.py 所在目录 = backend/
DB_PATH = BASE_DIR / "papertrace.db"             # 本地默认：backend/papertrace.db
#
# DATABASE_URL 可以被环境变量覆盖，部署时很有用：
#   - Render 上挂一块 1GB 持久盘到 /var/data，就配 DATABASE_URL=sqlite:////var/data/papertrace.db
#     （注意 sqlite:////  是 4 个斜杠，因为绝对路径以 / 开头）
#   - 不挂盘的话默认就用本地 backend/papertrace.db，Render 重启会丢失数据库
#     （对 PaperTrace 这种"每次查询都现拉论文"的工具来说，丢库其实可以接受）
import os as _os
DATABASE_URL = _os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)


# ===== 引擎和会话工厂 =====
# create_engine 创建一个"数据库连接管理器"，整个 app 共用一个
# echo=False 关闭 SQL 日志；调试时可以临时改 True 看 SQLAlchemy 实际发的 SQL
# connect_args 是 SQLite 专属参数：允许多线程访问（FastAPI 需要）
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, connect_args=_connect_args)

# sessionmaker 是"会话工厂"，调它一次产出一个 Session 对象
# autoflush=False：手动控制何时写盘，避免意外触发
# expire_on_commit=False：commit 后对象仍可读字段，避免 FastAPI 路由里炸
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


# ===== 模型基类 =====
class Base(DeclarativeBase):
    """所有表的基类，SQLAlchemy 通过它收集所有模型。"""
    pass


# ===== 表 1：papers =====
class Paper(Base):
    """一篇论文。对应数据库的 papers 表。"""

    __tablename__ = "papers"  # 实际表名

    # 自增主键。Mapped[int] 告诉类型检查器它是 int；mapped_column 配置数据库层
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Semantic Scholar 的论文 ID，业务上唯一（不能重复拉同一篇论文）
    paper_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)

    # 论文标题。String 限制 500 字符，应付绝大多数题目
    title: Mapped[str] = mapped_column(String(500))

    # 摘要可能很长，用 Text（不限长度）
    abstract: Mapped[str] = mapped_column(Text)

    # 年份可能为空（有的论文没填）→ Optional[int]
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 作者列表。SQLite 里没有原生数组类型，最简单的做法是存成 "Alice, Bob, Carol" 这样的字符串
    # 切片 4/6 读取时再 .split(", ") 还原。这是新手最易理解的做法。
    authors: Mapped[str] = mapped_column(String(2000), default="")

    # 引用次数，默认 0
    citation_count: Mapped[int] = mapped_column(Integer, default=0)

    # 入库时间。用 timezone-aware 的 UTC，避免时区坑
    # default 接受一个可调用对象：每次插入新行时调用，得到当前时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # ===== 关系 =====
    # 一篇 paper 有多条 claims。这是 ORM 层的"虚拟字段"，不在数据库里真实存在
    # back_populates 让两边互相引用（Claim.paper 也能拿到所属论文）
    # cascade="all, delete-orphan"：删 paper 时连带删它的 claims
    claims: Mapped[list["Claim"]] = relationship(
        back_populates="paper",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        # 方便 print(paper) 时看清楚
        return f"<Paper id={self.id} title={self.title[:30]!r} year={self.year}>"


# ===== 表 2：claims（主张）=====
class Claim(Base):
    """从论文摘要中抽出来的一条"主张"，比如 '远程办公提升 X 群体的生产力'。"""

    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 外键：指向 papers.id。ondelete="CASCADE" 让数据库层在删 paper 时自动删相关 claim
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"),
        index=True,  # 加索引：经常按 paper_id 查 claims，索引能加速
    )

    # 研究对象，例如 "软件工程师"
    subject: Mapped[str] = mapped_column(String(500))

    # 干预/变量，例如 "完全远程办公"
    intervention: Mapped[str] = mapped_column(String(500))

    # 结论描述，例如 "生产力提升 13%"
    conclusion: Mapped[str] = mapped_column(Text)

    # 方向：positive / negative / neutral
    # 严格点可以用 SQLAlchemy 的 Enum，但用字符串更直观，由抽取层保证取值
    direction: Mapped[str] = mapped_column(String(20))

    # 反向关系：从一条 claim 反向拿到它所属的 paper 对象
    paper: Mapped["Paper"] = relationship(back_populates="claims")

    def __repr__(self) -> str:
        return f"<Claim id={self.id} subject={self.subject!r} direction={self.direction}>"


# ===== 表 3.5：relation_cache（claim-pair 判定结果的持久化缓存）=====
# ---------------------------------------------------------------
# 为什么要单独再开一张表？
#   contradictions 表是"本次任务的"矩阵结果，按 claim_id 关联。
#   但 claim_id 是每次任务新生成的，跨任务重复出现的 claim 拿不到旧 id。
#   relation_cache 用 claim 的"内容指纹"做 key，跨任务、跨 query 通用。
#
# 字段设计:
#   - pair_hash: sha256(sig_lo || '\0' || sig_hi || '\0' || model)，定长 64 字符做主键
#   - sig_lo / sig_hi: 排好序的 claim 内容指纹（subject|intervention|conclusion|direction），
#                     方便 dump 看脏数据，也方便日后做更细粒度的分析
#   - model: 记录是哪个模型判的，换模型时能整批失效旧数据
#
# 命中场景:
#   - 同一 query 反复点"分析"
#   - 不同 query 但有重叠论文 → 同一 claim 出现 → 同一 pair 命中
#   - 用户 refresh=True：跳过读，但仍写入（用最新结果覆盖）
class RelationCache(Base):
    """两条 claim 之间的判定结果缓存（按内容指纹去重）。"""

    __tablename__ = "relation_cache"

    # 64 字符的 sha256 作为主键，避免长字符串作 PK 带来的索引膨胀
    pair_hash: Mapped[str] = mapped_column(String(64), primary_key=True)

    # 排好序的两条 claim 指纹（lo ≤ hi 字母序），便于 dump 排查
    sig_lo: Mapped[str] = mapped_column(Text)
    sig_hi: Mapped[str] = mapped_column(Text)

    # 哪个模型判的：换模型时可以整批失效
    model: Mapped[str] = mapped_column(String(100), index=True)

    relation: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<RelationCache {self.pair_hash[:8]} {self.relation} "
            f"conf={self.confidence:.2f} model={self.model}>"
        )


# ===== 表 3：contradictions（主张之间的关系矩阵）=====
class Contradiction(Base):
    """两条 claim 之间的关系：support / contradict / unrelated。"""

    __tablename__ = "contradictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 两个外键，分别指向 claims 表的两条主张
    claim_a_id: Mapped[int] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"),
        index=True,
    )
    claim_b_id: Mapped[int] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"),
        index=True,
    )

    # 关系类型
    relation: Mapped[str] = mapped_column(String(20))

    # LLM 给出的置信度，0~1 的浮点数
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    # 同一对 (a, b) 不应有两条记录。复合唯一约束在表级别声明
    __table_args__ = (
        UniqueConstraint("claim_a_id", "claim_b_id", name="uq_claim_pair"),
    )

    def __repr__(self) -> str:
        return (
            f"<Contradiction {self.claim_a_id}↔{self.claim_b_id} "
            f"{self.relation} conf={self.confidence:.2f}>"
        )


# ===== 工具函数 =====
def init_db() -> None:
    """创建所有表（如果还不存在）。app 启动时调一次即可。"""
    # Agent 模型与现有模型共享 Base；延迟导入避免 database <-> agent.models 循环导入。
    import agent.models  # noqa: F401

    # Base.metadata 收集了所有继承 Base 的模型的表结构
    # create_all 只会创建不存在的表，已有的表不会被改动
    Base.metadata.create_all(bind=engine)
    print(f"[init_db] 数据库已就绪：{DB_PATH}")


def get_session() -> Session:
    """
    获取一个新的数据库会话。

    用法 1（脚本里）：
        session = get_session()
        try:
            ...
            session.commit()
        finally:
            session.close()

    用法 2（FastAPI 依赖注入，切片 6 会用到）：
        def db_dep():
            db = get_session()
            try:
                yield db
            finally:
                db.close()
    """
    return SessionLocal()


# ===== 文件直接运行时的自检 =====
if __name__ == "__main__":
    import sys
    # Windows 控制台中文修正
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    print(">>> 初始化数据库 ...")
    init_db()

    print("\n>>> 插入一条测试论文 + 一条测试主张 + 一条测试关系 ...")
    session = get_session()
    try:
        # 1) 造一篇假论文
        demo_paper = Paper(
            paper_id="demo-001",
            title="A Demo Paper About Remote Work",
            abstract="We find that remote work increases productivity by 13%.",
            year=2024,
            authors="Alice, Bob",
            citation_count=42,
        )
        session.add(demo_paper)
        session.flush()  # flush 把 INSERT 真正发到数据库，但还没 commit；这样能立刻拿到自增 id

        # 2) 给这篇论文挂两条主张（主张 A：正向；主张 B：负向，方便测矛盾）
        claim_a = Claim(
            paper_id=demo_paper.id,
            subject="软件工程师",
            intervention="完全远程办公",
            conclusion="生产力提升 13%",
            direction="positive",
        )
        claim_b = Claim(
            paper_id=demo_paper.id,
            subject="软件工程师",
            intervention="完全远程办公",
            conclusion="生产力下降 5%",
            direction="negative",
        )
        session.add_all([claim_a, claim_b])
        session.flush()

        # 3) 在两条主张之间记录一个 "矛盾" 关系
        rel = Contradiction(
            claim_a_id=claim_a.id,
            claim_b_id=claim_b.id,
            relation="contradict",
            confidence=0.92,
        )
        session.add(rel)

        # 真正写盘
        session.commit()

        # 4) 验证：再查一次，确认数据真的进库了
        print("\n>>> 验证查询结果：")
        from sqlalchemy import select
        for paper in session.scalars(select(Paper)).all():
            print(paper)
            for c in paper.claims:  # 用 ORM 关系直接拿到所有挂在这篇论文下的主张
                print(" ", c)
        for rel in session.scalars(select(Contradiction)).all():
            print(rel)

        print("\n[OK] 数据库自检通过。")
        print(f"     SQLite 文件位置：{DB_PATH}")
        print("     可以用 DB Browser for SQLite 打开看")

    except Exception as e:
        # 任何异常都要回滚，避免半成品脏数据
        session.rollback()
        print(f"[ERROR] 自检失败：{e}")
        raise
    finally:
        session.close()


# ===========================================================
# 如何运行
# ===========================================================
# 1. 激活 venv：     source venv/Scripts/activate
# 2. 在 backend 目录：python database.py
#
# 你会看到：
#   - 数据库文件 backend/papertrace.db 被创建
#   - 一篇 demo 论文 + 两条主张 + 一条矛盾关系被插入并查回来
#
# 可重复运行：
#   再跑一次会因为 paper_id="demo-001" 唯一约束冲突而报错。
#   想反复测，可以手动删掉 papertrace.db 文件再跑。
# ===========================================================
