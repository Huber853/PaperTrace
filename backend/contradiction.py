"""
PaperTrace - 切片 5：主张之间的关系判定
=========================================

作用：给定两条主张（claims），用 DeepSeek 判断它们之间是
       support / contradict / unrelated 的哪一种，并附上置信度和理由。
       同时提供 build_matrix() 把 N 条主张两两比对成 N×N 关系矩阵。

被谁调用：
    - 切片 6 的 FastAPI /api/analyze：抽完主张后调 build_matrix() 构建矩阵
    - 切片 8 的前端用这个矩阵画热力图

----------------------------------------------------
为什么不直接拿"两段摘要"比，而要先抽 claims 再两两比？
----------------------------------------------------
两个原因：
1) 一篇论文里可能同时有正向和负向发现（比如远程办公"提升生产力但加剧孤独"）。
   如果把整段摘要塞给 LLM 比对，模型很难给出干净的判断。
   先拆成原子主张，再两两比对，结论才精确。

2) 拆完之后我们能在前端用热力图清晰地展示"哪一对主张矛盾"，
   而不是"哪一对论文矛盾"——粒度更细，答辩更炸。

----------------------------------------------------
为什么需要两个优化（subject 过滤 + 缓存）？
----------------------------------------------------
20 篇论文 × 平均 3 条主张 = 60 条 claims。
两两组合是 C(60, 2) = 1770 对。
如果每对都调 LLM，成本和时间都不可接受。

优化 1：subject 词汇完全不重叠的两条主张直接判定为 unrelated（不调 API）
        → 通常能砍掉 50%~70% 的调用

优化 2：缓存相同的 (a, b) 不重复调（只在内存里，进程级别）
        → 同一次 build_matrix 内基本无重复，但跨请求重复时能省钱

3000 对 × 0.07 元/对 ≈ 几毛钱级别，完全能接受（详见文件末尾的成本估算）。
"""

# ===== 导入区 =====
from __future__ import annotations

import asyncio                              # 并发跑多对判定，加速
import json
import os
import re
import sys
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator


# ===== 加载环境变量（和 extractor 共用同一份 .env）=====
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

if not DEEPSEEK_API_KEY:
    raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量")


# ===== Pydantic 模型 =====
class RelationResult(BaseModel):
    """LLM 返回的单次判定结果。"""

    relation: Literal["support", "contradict", "unrelated"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("relation", mode="before")
    @classmethod
    def _normalize_relation(cls, v):
        # 兜底：把模型可能返回的同义词归一化
        if isinstance(v, str):
            v = v.strip().lower()
            mapping = {
                "supports": "support", "agree": "support", "consistent": "support",
                "contradicts": "contradict", "disagree": "contradict",
                "inconsistent": "contradict", "conflict": "contradict",
                "unrelated": "unrelated", "irrelevant": "unrelated",
                "no relation": "unrelated", "none": "unrelated",
            }
            v = mapping.get(v, v)
        return v


# ===== 高质量判定 prompt =====
# 设计要点：
# 1. 明确三个关系的定义边界，避免模型把"主题相同但发现互补"判成 contradict
# 2. 给出 3 个 few-shot 示例，覆盖三个类别各一个
# 3. 强调"必须只输出 JSON"
# 4. 强调"如果两条主张研究的不是同一件事，就是 unrelated"——避免硬找联系
SYSTEM_PROMPT = """You are a scientific claim relation classifier.

You are given two scientific claims, each with: subject, intervention, conclusion, direction.
Your job: determine the relation between Claim A and Claim B, and output a JSON object.

Possible relations:
- "support":    Both claims describe the same phenomenon and point in the SAME direction.
                Example: A says "X increases Y", B says "X also boosts Y in another sample".
- "contradict": Both claims describe the same phenomenon but point in OPPOSING directions.
                Example: A says "X increases Y", B says "X decreases Y".
- "unrelated":  The claims discuss different subjects, different interventions, or different
                outcomes, and cannot be meaningfully compared.

JUDGEMENT RULES:
1. For "support" or "contradict", BOTH claims must address the same subject AND the same intervention/variable AND the same outcome dimension. If any of these differ substantially, return "unrelated".
2. "Same direction with mild differences in magnitude" = support, not contradict.
3. "Mixed effects" or "neutral findings" vs "positive findings" on the same thing = contradict (since one says effect exists, other says it doesn't).
4. If unsure, prefer "unrelated" with low confidence rather than guessing.

OUTPUT RULES:
- Output ONLY a JSON object with three keys: "relation", "confidence", "reason".
- "relation" must be one of: "support" | "contradict" | "unrelated".
- "confidence" must be a number between 0.0 and 1.0.
- "reason" must be a single concise sentence (under 30 words) explaining the judgement.
- No markdown, no code fences, no extra prose.

EXAMPLE 1:
Claim A: subject="software engineers", intervention="fully remote work", conclusion="productivity rose 13%", direction="positive"
Claim B: subject="software engineers", intervention="fully remote work", conclusion="productivity dropped 8%", direction="negative"
Output: {"relation":"contradict","confidence":0.95,"reason":"Same subject and intervention but opposite productivity effects."}

EXAMPLE 2:
Claim A: subject="knowledge workers", intervention="remote work", conclusion="self-rated productivity up 17%", direction="positive"
Claim B: subject="knowledge workers", intervention="remote work", conclusion="output measures improved by 12%", direction="positive"
Output: {"relation":"support","confidence":0.9,"reason":"Both report positive productivity effects of remote work for similar populations."}

EXAMPLE 3:
Claim A: subject="lithium-ion batteries", intervention="fast charging", conclusion="capacity degrades faster", direction="negative"
Claim B: subject="software engineers", intervention="fully remote work", conclusion="isolation increases", direction="negative"
Output: {"relation":"unrelated","confidence":0.99,"reason":"Different subjects and interventions; not comparable."}
"""


# ===== 缓存：进程级 =====
# key = (claim_a 的内容指纹, claim_b 的内容指纹)，value = 判定结果 dict
# 注意做对称：(A,B) 和 (B,A) 应共享缓存（关系是对称的）
_RELATION_CACHE: dict[frozenset, dict] = {}


def _claim_signature(claim: dict) -> str:
    """为一条 claim 生成可哈希的内容指纹，用于缓存 key。"""
    # 取四个字段拼成字符串，去空格统一大小写
    parts = [
        claim.get("subject", ""),
        claim.get("intervention", ""),
        claim.get("conclusion", ""),
        claim.get("direction", ""),
    ]
    return "|".join(p.strip().lower() for p in parts)


# ===== Subject 词汇过滤：节省 token 的核心优化 =====
# 极简英文停用词表，过滤掉常见无意义词，避免 "the/a/and" 等假阳性匹配
_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with",
    "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "by", "as", "from", "this", "that", "these", "those", "it",
    "its", "their", "his", "her", "his/her",
}


def _tokenize(text: str) -> set[str]:
    """把字符串拆成小写单词集合，去停用词和短词。"""
    # \w+ 匹配字母数字下划线，避免标点干扰
    words = re.findall(r"\w+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _subjects_clearly_unrelated(claim_a: dict, claim_b: dict) -> bool:
    """
    判断两条 claim 的研究主题是否"明显不相关"。
    规则：把 subject + intervention 一起做词汇集合，
          如果两个集合的交集为空 → 返回 True，可以跳过 LLM 调用。
    这是非常保守的过滤：只要有任何一个共同词就让 LLM 去判。
    """
    text_a = f"{claim_a.get('subject', '')} {claim_a.get('intervention', '')}"
    text_b = f"{claim_b.get('subject', '')} {claim_b.get('intervention', '')}"
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a or not tokens_b:
        return False  # 空文本不敢判，交给 LLM
    return tokens_a.isdisjoint(tokens_b)  # 完全无交集 → 不相关


# ===== 核心函数 1：单对判定 =====
async def judge_relation(
    claim_a: dict,
    claim_b: dict,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """
    判断两条 claim 之间的关系。

    参数:
        claim_a, claim_b: 两条 claim 的 dict（含 subject/intervention/conclusion/direction）
        client: 可选的 httpx.AsyncClient，复用连接池。批量调用时强烈建议传入。

    返回:
        {"relation": "support|contradict|unrelated", "confidence": 0.0-1.0, "reason": "..."}
    """

    # 优化 1：subject 完全不重叠 → 直接返回 unrelated，省一次 API 调用
    if _subjects_clearly_unrelated(claim_a, claim_b):
        return {
            "relation": "unrelated",
            "confidence": 0.99,
            "reason": "Subjects/interventions share no common keywords (skipped LLM).",
        }

    # 优化 2：缓存命中（用 frozenset 实现 a/b 对称）
    cache_key = frozenset({_claim_signature(claim_a), _claim_signature(claim_b)})
    if cache_key in _RELATION_CACHE:
        return _RELATION_CACHE[cache_key]

    # 构造 user message：把两条 claim 格式化得清清楚楚
    user_msg = (
        f"Claim A:\n"
        f'  subject="{claim_a.get("subject", "")}"\n'
        f'  intervention="{claim_a.get("intervention", "")}"\n'
        f'  conclusion="{claim_a.get("conclusion", "")}"\n'
        f'  direction="{claim_a.get("direction", "")}"\n\n'
        f"Claim B:\n"
        f'  subject="{claim_b.get("subject", "")}"\n'
        f'  intervention="{claim_b.get("intervention", "")}"\n'
        f'  conclusion="{claim_b.get("conclusion", "")}"\n'
        f'  direction="{claim_b.get("direction", "")}"'
    )

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{DEEPSEEK_BASE_URL}/chat/completions"

    # 复用外部传入的 client，没有就临时建一个
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=60.0)

    try:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]

        try:
            raw = json.loads(content)
            validated = RelationResult.model_validate(raw)
            result = validated.model_dump()
        except (json.JSONDecodeError, ValidationError) as e:
            # 解析失败时给一个安全降级值，不让上游崩
            print(f"[contradiction] 解析失败，降级为 unrelated: {e}")
            result = {
                "relation": "unrelated",
                "confidence": 0.0,
                "reason": f"LLM response unparseable: {type(e).__name__}",
            }

        _RELATION_CACHE[cache_key] = result
        return result

    finally:
        if own_client:
            await client.aclose()


# ===== 核心函数 2：构建 N×N 矩阵 =====
async def build_matrix(claims: list[dict]) -> list[list[dict]]:
    """
    对一组 claim 两两判定，返回 N×N 的关系矩阵。

    参数:
        claims: claim 字典列表

    返回:
        matrix: N×N 嵌套列表
                matrix[i][j] = judge_relation(claims[i], claims[j]) 的结果
                对角线 matrix[i][i] = 自相关，自动填 support / 1.0
                因为关系对称，matrix[j][i] 直接复用 matrix[i][j]

    优化:
        - 对角线不调 API
        - 上三角调用，下三角镜像
        - judge_relation 内部还有 subject 过滤 + 缓存
        - 用 asyncio.gather 并发跑所有调用，速度大幅提升
    """
    n = len(claims)
    # 先建一个空矩阵，全部填 None 占位
    matrix: list[list[dict | None]] = [[None] * n for _ in range(n)]

    # 对角线：每条 claim 和自己对比 → 当然 support
    for i in range(n):
        matrix[i][i] = {
            "relation": "support",
            "confidence": 1.0,
            "reason": "Self-comparison.",
        }

    # 收集所有需要计算的上三角对 (i, j)，i < j
    pairs_to_compute: list[tuple[int, int]] = [
        (i, j) for i in range(n) for j in range(i + 1, n)
    ]

    # 用一个共享的 AsyncClient 跑所有并发请求
    async with httpx.AsyncClient(timeout=60.0) as client:

        # 并发上限：避免一次性发太多请求被限流
        # DeepSeek 单 key 并发约 16 左右，我们保守用 8
        semaphore = asyncio.Semaphore(8)

        async def _judge_with_limit(i: int, j: int):
            async with semaphore:
                result = await judge_relation(claims[i], claims[j], client=client)
                return i, j, result

        # asyncio.gather 并发执行所有判定
        tasks = [_judge_with_limit(i, j) for (i, j) in pairs_to_compute]
        results = await asyncio.gather(*tasks)

    # 把结果填进矩阵的上三角，并镜像到下三角
    for i, j, result in results:
        matrix[i][j] = result
        matrix[j][i] = result  # 关系对称：A↔B 与 B↔A 是同一关系

    # 此时矩阵里所有位置都被填好了，类型转回 list[list[dict]]
    return matrix  # type: ignore[return-value]


# ===== 自检入口 =====
if __name__ == "__main__":
    from pprint import pprint

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    # 5 条用来测试的 claims：
    #   #0 和 #1 互相矛盾（remote work productivity 一正一负）
    #   #0 和 #2 互相支持（都正向）
    #   #3 是混合效应（neutral）
    #   #4 是完全无关的话题（电池），用来验证 subject 过滤会跳过它
    DEMO_CLAIMS = [
        {  # 0
            "subject": "software engineers",
            "intervention": "fully remote work",
            "conclusion": "self-rated productivity increased by 13%",
            "direction": "positive",
        },
        {  # 1
            "subject": "software engineers",
            "intervention": "fully remote work",
            "conclusion": "measured output dropped by 8%",
            "direction": "negative",
        },
        {  # 2
            "subject": "knowledge workers",
            "intervention": "remote work arrangements",
            "conclusion": "task completion rates improved",
            "direction": "positive",
        },
        {  # 3
            "subject": "hybrid workers",
            "intervention": "hybrid work arrangements",
            "conclusion": "no significant change in productivity",
            "direction": "neutral",
        },
        {  # 4
            "subject": "lithium-ion batteries",
            "intervention": "fast charging at 4C",
            "conclusion": "cycle life dropped 30%",
            "direction": "negative",
        },
    ]

    print(">>> 测试 1：单对判定（明显矛盾的两条主张）\n")
    pair_result = asyncio.run(judge_relation(DEMO_CLAIMS[0], DEMO_CLAIMS[1]))
    pprint(pair_result)

    print("\n>>> 测试 2：5 条 claims 构建 5x5 关系矩阵\n")
    matrix = asyncio.run(build_matrix(DEMO_CLAIMS))

    # 紧凑可视化：每个格子显示 relation 首字母 + 置信度
    LETTER = {"support": "S", "contradict": "C", "unrelated": "U"}
    print("矩阵紧凑视图（S=support, C=contradict, U=unrelated）:\n")
    print("     " + "   ".join(f"#{j}" for j in range(len(DEMO_CLAIMS))))
    for i, row in enumerate(matrix):
        cells = []
        for cell in row:
            cells.append(f"{LETTER[cell['relation']]}{cell['confidence']:.1f}")
        print(f"#{i}  " + " ".join(cells))

    print("\n>>> 关键判定的理由：")
    pairs_of_interest = [
        (0, 1, "矛盾对：13% 提升 vs 8% 下降"),
        (0, 2, "支持对：两条都正向"),
        (0, 4, "无关对：远程办公 vs 电池"),
    ]
    for i, j, label in pairs_of_interest:
        cell = matrix[i][j]
        print(f"  #{i}↔#{j}  [{label}]")
        print(f"          {cell['relation']} (conf={cell['confidence']:.2f}) — {cell['reason']}")

    print(f"\n>>> 缓存命中数：{len(_RELATION_CACHE)} 条独立 LLM 调用结果")


# ===========================================================
# 成本估算（假设 20 篇论文）
# ===========================================================
# 假设：
#   - 20 篇论文 × 平均 3 条主张 = 60 条 claims
#   - 总对数 C(60, 2) = 1770 对
#   - subject 过滤命中率 ~60% → 实际调 LLM 的对数 ≈ 700 对
#   - 每次调用：约 600 input tokens（system prompt + 两条 claim）
#                + 80 output tokens（短 JSON）
#
# DeepSeek-chat 当前定价（人民币）：
#   - 输入 tokens：约 ¥1.0 / 1M
#   - 输出 tokens：约 ¥2.0 / 1M
#
# 单次成本：
#   600 × 1.0 / 1M + 80 × 2.0 / 1M ≈ 0.00076 元
#
# 一次完整 build_matrix(20 篇论文)：
#   700 × 0.00076 ≈ 0.53 元
#
# 结论：一次完整分析大约花 5 毛 - 1 块钱，完全可承受。
# 真正能让钱爆炸的是用户反复点"分析"。
# 后续可以加：每个 query 入库前查重，相同 query 直接复用历史结果。
# ===========================================================


# ===========================================================
# 如何运行
# ===========================================================
# 1. 确保 backend/.env 里有 DEEPSEEK_API_KEY
# 2. cd backend && source venv/Scripts/activate
# 3. python contradiction.py
#
# 你应该看到：
#   - 单对测试：claim #0 ↔ claim #1 → contradict, conf ≈ 0.9
#   - 5×5 矩阵：对角线全是 S1.0；#4（电池）那一行/列大多是 U（被 subject 过滤）
#   - 关键判定的理由解释清晰
# ===========================================================
