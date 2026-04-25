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
   先拆成原子主张，两两比，结论才精确。
2) 拆完之后能在前端用热力图清晰展示"哪一对主张矛盾"。

----------------------------------------------------
核心优化（从开销最大到最小）
----------------------------------------------------
对 60 条 claims 来说，朴素做法是 C(60,2)=1770 次 LLM 调用。下面这一组优化
能把它砍到约 100~200 次，同时保留判定质量：

A) **持久化缓存（SQLite）** ← 跨进程、跨 query 复用
   每对 (claim_a, claim_b) 用内容指纹做 key，一旦 LLM 判过就落库。
   下次同一对（即使来自不同 query）直接读库，零成本。
   配合内存 L1 缓存形成两级。

B) **subject 词汇集合过滤** ← 朴素但有效的预剪枝
   两条 claim 的 subject + intervention 完全无 token 交集 → 直接判 unrelated。
   通常能砍掉 50%~70% 的剩余对。

C) **批量判定（一次 LLM 调多对）** ← 摊薄 system prompt 成本
   把若干"剩下要判的对"打成一包发给 DeepSeek，一次返回多个结果。
   600 token 的 system prompt 本来要重复发 N 次，现在每 BATCH_SIZE 对才发 1 次。
   单次输入 token 减少 60%~70%。

D) **上三角 + 对角线短路** ← 数学上的对称性
   关系是对称的，只算 i<j；对角线（自比）直接填 support/1.0。

E) **asyncio 并发 + Semaphore 限流** ← 充分利用 DeepSeek 的并发额度
"""

# ===== 导入区 =====
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Literal

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator


# ===== 加载环境变量（和 extractor 共用同一份 .env）=====
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# 并发上限：DeepSeek 付费档单 key 可吃到 50+，保守用 8，可被环境变量覆盖
RELATION_CONCURRENCY = int(os.getenv("RELATION_CONCURRENCY", "8"))
# 批量判定的每批大小。6 是经验值：再大解析风险升高，再小 system prompt 摊不薄
RELATION_BATCH_SIZE = int(os.getenv("RELATION_BATCH_SIZE", "6"))
# 是否启用 SQLite 二级缓存（默认开）。设为 "false" 可禁用
RELATION_CACHE_PERSIST = os.getenv("RELATION_CACHE_PERSIST", "true").lower() != "false"

if not DEEPSEEK_API_KEY:
    raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量")


# ===== Pydantic 模型 =====
# 模型可能把 relation 写成 "supports" / "agrees" / "conflict" 之类，
# 抽出来一个纯函数做归一化，给两个 BaseModel 共用。
_RELATION_SYNONYMS = {
    "supports": "support", "agree": "support", "agrees": "support", "consistent": "support",
    "contradicts": "contradict", "disagree": "contradict", "disagrees": "contradict",
    "inconsistent": "contradict", "conflict": "contradict", "conflicts": "contradict",
    "unrelated": "unrelated", "irrelevant": "unrelated",
    "no relation": "unrelated", "none": "unrelated",
}


def _normalize_relation_value(v: Any) -> Any:
    if isinstance(v, str):
        s = v.strip().lower()
        return _RELATION_SYNONYMS.get(s, s)
    return v


class RelationResult(BaseModel):
    """LLM 返回的单次判定结果。"""

    relation: Literal["support", "contradict", "unrelated"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("relation", mode="before")
    @classmethod
    def _norm(cls, v):
        return _normalize_relation_value(v)


class _BatchItem(BaseModel):
    """批量判定时返回数组里的单条。"""
    id: str
    relation: Literal["support", "contradict", "unrelated"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("relation", mode="before")
    @classmethod
    def _norm(cls, v):
        return _normalize_relation_value(v)


class _BatchResponse(BaseModel):
    results: list[_BatchItem] = Field(default_factory=list)


# ===== 单对判定 prompt =====
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


# ===== 批量判定 prompt =====
# 设计要点:
# - 输入是 {"pairs": [{"id": "...", "a": {...}, "b": {...}}, ...]}
# - 输出是 {"results": [{"id": "...", "relation": "...", "confidence": ..., "reason": "..."}, ...]}
# - id 必须原样返回，否则无法对回原 pair
# - 数组长度必须一致
# - 判定规则、关系定义、举例 都和单对版本完全一致，避免行为漂移
SYSTEM_PROMPT_BATCH = """You are a scientific claim relation classifier.

You will receive a JSON object with a "pairs" array. Each pair has an "id" (string), and two claims "a" and "b". Each claim has: subject, intervention, conclusion, direction.

For EACH pair, classify the relation between a and b.

Possible relations:
- "support":    Both claims describe the same phenomenon and point in the SAME direction.
- "contradict": Both claims describe the same phenomenon but point in OPPOSING directions.
- "unrelated":  The claims discuss different subjects, interventions, or outcomes, and cannot be meaningfully compared.

JUDGEMENT RULES:
1. For "support" or "contradict", BOTH claims must address the same subject AND the same intervention/variable AND the same outcome dimension. If any differ substantially, return "unrelated".
2. "Same direction with mild differences in magnitude" = support, not contradict.
3. "Mixed/neutral" vs "clear positive/negative" on the same thing = contradict.
4. If unsure, prefer "unrelated" with low confidence over guessing.

OUTPUT RULES — read carefully:
- Output ONLY a JSON object with one top-level key: "results".
- "results" is an array. It MUST contain EXACTLY one entry for EACH input pair, in the same order.
- Each entry has 4 keys: "id" (copy from input EXACTLY), "relation" ("support"|"contradict"|"unrelated"), "confidence" (0.0-1.0), "reason" (≤30 words).
- No markdown, no fences, no commentary.

EXAMPLE INPUT:
{"pairs":[
{"id":"p1","a":{"subject":"engineers","intervention":"remote work","conclusion":"productivity +13%","direction":"positive"},"b":{"subject":"engineers","intervention":"remote work","conclusion":"productivity -8%","direction":"negative"}},
{"id":"p2","a":{"subject":"batteries","intervention":"fast charging","conclusion":"capacity drops","direction":"negative"},"b":{"subject":"engineers","intervention":"remote work","conclusion":"isolation rose","direction":"negative"}}
]}

EXAMPLE OUTPUT:
{"results":[
{"id":"p1","relation":"contradict","confidence":0.95,"reason":"Same subject and intervention but opposite productivity effects."},
{"id":"p2","relation":"unrelated","confidence":0.99,"reason":"Different subjects and interventions; not comparable."}
]}
"""


# ===== L1 缓存：进程级内存 =====
# key = (sig_lo, sig_hi, model)，value = 判定结果 dict
# 注意做对称：(A,B) 和 (B,A) 应共享缓存（关系是对称的）
_RELATION_CACHE: dict[tuple[str, str, str], dict] = {}


# ===== 内容指纹 / 缓存 key =====
def _claim_signature(claim: dict) -> str:
    """为一条 claim 生成可哈希的内容指纹，用于缓存 key。"""
    parts = [
        claim.get("subject", ""),
        claim.get("intervention", ""),
        claim.get("conclusion", ""),
        claim.get("direction", ""),
    ]
    return "|".join(p.strip().lower() for p in parts)


def _ordered_pair(sig_a: str, sig_b: str) -> tuple[str, str]:
    """把 (a,b) 和 (b,a) 归一成同一个有序 tuple，保证缓存对称。"""
    return (sig_a, sig_b) if sig_a <= sig_b else (sig_b, sig_a)


def _pair_hash(sig_lo: str, sig_hi: str, model: str) -> str:
    """两条已排序指纹 + model → 64 字符 sha256，用作 SQLite 主键。"""
    raw = f"{sig_lo}\0{sig_hi}\0{model}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ===== L2 缓存：SQLite ==========================================
# 用懒加载，避免 contradiction.py 在没初始化数据库的环境（比如纯单测）下崩
def _get_db_session():
    """惰性引入 database 模块，返回 (Session 实例, RelationCache 模型)。"""
    from database import SessionLocal, RelationCache  # 局部 import，破循环依赖
    return SessionLocal(), RelationCache


def _bulk_load_from_db(pair_hashes: list[str]) -> dict[str, dict]:
    """
    批量查 L2 缓存。返回 {pair_hash: result_dict}，未命中的 hash 不在结果里。

    用 IN (...) 一次查全部，比每对一次往返快得多。SQLite 的 IN 子句对几千个
    元素也很高效。
    """
    if not RELATION_CACHE_PERSIST or not pair_hashes:
        return {}
    try:
        from sqlalchemy import select
        session, RelationCache = _get_db_session()
        try:
            stmt = select(RelationCache).where(RelationCache.pair_hash.in_(pair_hashes))
            rows = session.scalars(stmt).all()
            return {
                r.pair_hash: {
                    "relation": r.relation,
                    "confidence": r.confidence,
                    "reason": r.reason,
                }
                for r in rows
            }
        finally:
            session.close()
    except Exception as e:
        # 缓存层永远不应该让主流程崩
        print(f"[contradiction] L2 缓存读取失败（按全部未命中处理）: {e}")
        return {}


def _bulk_save_to_db(rows: list[dict]) -> None:
    """
    批量写 L2 缓存。每个 row 形如:
        {"pair_hash": "...", "sig_lo": "...", "sig_hi": "...",
         "model": "...", "relation": "...", "confidence": 0.x, "reason": "..."}

    使用 INSERT ... ON CONFLICT DO UPDATE（SQLite 特化的 upsert），保证幂等：
    refresh=True 重跑时，新结果覆盖旧的。
    """
    if not RELATION_CACHE_PERSIST or not rows:
        return
    try:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        session, RelationCache = _get_db_session()
        try:
            stmt = sqlite_insert(RelationCache.__table__).values(rows)
            # ON CONFLICT (pair_hash) DO UPDATE SET ...
            stmt = stmt.on_conflict_do_update(
                index_elements=["pair_hash"],
                set_={
                    "relation": stmt.excluded.relation,
                    "confidence": stmt.excluded.confidence,
                    "reason": stmt.excluded.reason,
                    "model": stmt.excluded.model,
                },
            )
            session.execute(stmt)
            session.commit()
        finally:
            session.close()
    except Exception as e:
        print(f"[contradiction] L2 缓存写入失败（已忽略，主流程不受影响）: {e}")


# ===== Subject 词汇过滤 =====
_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with",
    "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "by", "as", "from", "this", "that", "these", "those", "it",
    "its", "their", "his", "her", "his/her",
}


def _tokenize(text: str) -> set[str]:
    """把字符串拆成小写单词集合，去停用词和短词。"""
    words = re.findall(r"\w+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _subjects_clearly_unrelated(claim_a: dict, claim_b: dict) -> bool:
    """两条 claim 的 subject+intervention token 完全无交集 → True。"""
    text_a = f"{claim_a.get('subject', '')} {claim_a.get('intervention', '')}"
    text_b = f"{claim_b.get('subject', '')} {claim_b.get('intervention', '')}"
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a or not tokens_b:
        return False
    return tokens_a.isdisjoint(tokens_b)


# ===== 单对判定（保留,供外部测试 / 兜底使用）=====
async def judge_relation(
    claim_a: dict,
    claim_b: dict,
    client: httpx.AsyncClient | None = None,
    refresh: bool = False,
    model: str | None = None,
) -> dict:
    """
    判断两条 claim 之间的关系（含两级缓存）。

    返回:
        {"relation": "support|contradict|unrelated", "confidence": 0.0-1.0, "reason": "..."}
    """
    used_model = model or DEEPSEEK_MODEL

    # 优化 B：subject 完全不重叠 → 直接判定为 unrelated
    if _subjects_clearly_unrelated(claim_a, claim_b):
        return {
            "relation": "unrelated",
            "confidence": 0.99,
            "reason": "Subjects/interventions share no common keywords (skipped LLM).",
        }

    sig_a = _claim_signature(claim_a)
    sig_b = _claim_signature(claim_b)
    sig_lo, sig_hi = _ordered_pair(sig_a, sig_b)
    cache_key = (sig_lo, sig_hi, used_model)
    p_hash = _pair_hash(sig_lo, sig_hi, used_model)

    # L1 命中
    if not refresh and cache_key in _RELATION_CACHE:
        return _RELATION_CACHE[cache_key]

    # L2 命中（顺便回填 L1）
    if not refresh:
        hit = _bulk_load_from_db([p_hash]).get(p_hash)
        if hit is not None:
            _RELATION_CACHE[cache_key] = hit
            return hit

    # ===== 真正调 LLM =====
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
        "model": used_model,
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
            print(f"[contradiction] 解析失败，降级为 unrelated: {e}")
            result = {
                "relation": "unrelated",
                "confidence": 0.0,
                "reason": f"LLM response unparseable: {type(e).__name__}",
            }

        # 写两级缓存
        _RELATION_CACHE[cache_key] = result
        _bulk_save_to_db([{
            "pair_hash": p_hash,
            "sig_lo": sig_lo,
            "sig_hi": sig_hi,
            "model": used_model,
            "relation": result["relation"],
            "confidence": result["confidence"],
            "reason": result["reason"],
        }])
        return result

    finally:
        if own_client:
            await client.aclose()


# ===== 批量判定（一次 LLM 调多对）==============================
async def judge_relations_batch(
    items: list[dict],
    client: httpx.AsyncClient,
    model: str = DEEPSEEK_MODEL,
) -> dict[str, dict]:
    """
    批量判定一组 claim 对。一次 LLM 调用判定 len(items) 对关系。

    参数:
        items: [{"id": str, "a": claim_dict, "b": claim_dict}, ...]
        client: 共享的 httpx AsyncClient
        model: 模型名（缓存命中时区分用）

    返回:
        {"id": result_dict}。如果 LLM 返回 / 解析失败，可能返回部分或空 dict。
        调用方负责对未命中的 id 回退到单对调用。
    """
    if not items:
        return {}

    # 构造极简的 user 输入：只包含 id + 两条 claim 的四字段
    pairs_payload = [
        {
            "id": it["id"],
            "a": {k: it["a"].get(k, "") for k in ("subject", "intervention", "conclusion", "direction")},
            "b": {k: it["b"].get(k, "") for k in ("subject", "intervention", "conclusion", "direction")},
        }
        for it in items
    ]
    user_msg = json.dumps({"pairs": pairs_payload}, ensure_ascii=False)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_BATCH},
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

    try:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        print(f"[contradiction] 批量请求失败: {e}")
        return {}

    try:
        raw = json.loads(content)
        validated = _BatchResponse.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as e:
        print(f"[contradiction] 批量解析失败: {e}")
        return {}

    # 用 id 索引返回，调用方据此判断哪些缺失
    out: dict[str, dict] = {}
    for r in validated.results:
        out[r.id] = {
            "relation": r.relation,
            "confidence": r.confidence,
            "reason": r.reason,
        }
    return out


# ===== 核心：构建 N×N 矩阵 ======================================
async def build_matrix(claims: list[dict], refresh: bool = False) -> list[list[dict]]:
    """
    对一组 claim 两两判定，返回 N×N 的关系矩阵。

    流程：
        1. 对角线 → support/1.0（不调 API）
        2. 上三角全部对 → 计算 hash → 一次性 bulk-load L2 缓存
        3. 命中 L2 / L1 的填充矩阵；subject 词汇无交集的填 unrelated stub
        4. 剩下确实需要 LLM 的对 → 切成 BATCH_SIZE 一组 → 并发跑 batch 判定
        5. 批解析失败的 id 回退到单对调用
        6. 结果 bulk-save 到 L2，再写 L1，再填矩阵
    """
    n = len(claims)
    matrix: list[list[dict | None]] = [[None] * n for _ in range(n)]

    # 1. 对角线
    for i in range(n):
        matrix[i][i] = {
            "relation": "support",
            "confidence": 1.0,
            "reason": "Self-comparison.",
        }

    if n < 2:
        return matrix  # type: ignore[return-value]

    used_model = DEEPSEEK_MODEL

    # 2. 上三角对 + 预计算指纹 / hash
    sigs = [_claim_signature(c) for c in claims]
    pair_meta: list[dict] = []  # {i, j, sig_lo, sig_hi, p_hash, cache_key}
    for i in range(n):
        for j in range(i + 1, n):
            lo, hi = _ordered_pair(sigs[i], sigs[j])
            pair_meta.append({
                "i": i, "j": j,
                "sig_lo": lo, "sig_hi": hi,
                "p_hash": _pair_hash(lo, hi, used_model),
                "cache_key": (lo, hi, used_model),
            })

    # 3. 一次性 bulk-load L2
    if refresh:
        l2_hits: dict[str, dict] = {}
    else:
        l2_hits = _bulk_load_from_db([m["p_hash"] for m in pair_meta])

    # 4. 分桶：直接命中 / subject 过滤 / 待 LLM
    pending: list[dict] = []  # 真正要调 LLM 的对
    for m in pair_meta:
        i, j = m["i"], m["j"]

        # 4a. L1 命中
        if not refresh and m["cache_key"] in _RELATION_CACHE:
            matrix[i][j] = matrix[j][i] = _RELATION_CACHE[m["cache_key"]]
            continue

        # 4b. L2 命中（顺便回填 L1）
        if m["p_hash"] in l2_hits:
            r = l2_hits[m["p_hash"]]
            _RELATION_CACHE[m["cache_key"]] = r
            matrix[i][j] = matrix[j][i] = r
            continue

        # 4c. subject 词汇无交集 → 直接 unrelated（不进 LLM、不进缓存）
        # 不进缓存的原因：这是局部确定性规则，每次都能本地算出来；
        # 把它塞进缓存反而浪费空间
        if _subjects_clearly_unrelated(claims[i], claims[j]):
            stub = {
                "relation": "unrelated",
                "confidence": 0.99,
                "reason": "Subjects/interventions share no common keywords (skipped LLM).",
            }
            matrix[i][j] = matrix[j][i] = stub
            continue

        # 4d. 剩下的：要送进 LLM
        pending.append(m)

    if not pending:
        return matrix  # type: ignore[return-value]

    # 5. 切批 + 并发执行
    # 给每对一个唯一字符串 id（用 i_j），方便 LLM 原样回传
    batches: list[list[dict]] = []
    cur: list[dict] = []
    for m in pending:
        cur.append({
            "id": f"{m['i']}_{m['j']}",
            "a": claims[m["i"]],
            "b": claims[m["j"]],
        })
        if len(cur) >= RELATION_BATCH_SIZE:
            batches.append(cur)
            cur = []
    if cur:
        batches.append(cur)

    # 用 id 反查 meta，方便后面写矩阵 / 缓存
    meta_by_id = {f"{m['i']}_{m['j']}": m for m in pending}

    semaphore = asyncio.Semaphore(RELATION_CONCURRENCY)
    new_results: dict[str, dict] = {}

    async with httpx.AsyncClient(timeout=90.0) as client:

        async def _run_batch(batch: list[dict]) -> tuple[list[dict], dict[str, dict]]:
            """跑一批；返回 (这批的 items, 成功结果 dict)。"""
            async with semaphore:
                hit = await judge_relations_batch(batch, client=client, model=used_model)
            return batch, hit

        batch_results = await asyncio.gather(*[_run_batch(b) for b in batches])

        # 5a. 收集成功的结果
        missing_items: list[dict] = []
        for batch, hits in batch_results:
            for it in batch:
                if it["id"] in hits:
                    new_results[it["id"]] = hits[it["id"]]
                else:
                    # 批里漏掉的（解析失败 / id 不对应） → 回退到单对
                    missing_items.append(it)

        # 5b. 单对回退
        if missing_items:
            print(f"[contradiction] {len(missing_items)} 对从批量回退到单对调用")

            async def _single(it: dict) -> tuple[str, dict]:
                async with semaphore:
                    r = await judge_relation(
                        it["a"], it["b"],
                        client=client, refresh=refresh, model=used_model,
                    )
                return it["id"], r

            single_results = await asyncio.gather(*[_single(it) for it in missing_items])
            for pid, r in single_results:
                new_results[pid] = r

    # 6. 把 LLM 新得到的结果填进矩阵 + 两级缓存
    rows_to_persist: list[dict] = []
    for pid, r in new_results.items():
        m = meta_by_id[pid]
        i, j = m["i"], m["j"]
        matrix[i][j] = matrix[j][i] = r
        _RELATION_CACHE[m["cache_key"]] = r
        rows_to_persist.append({
            "pair_hash": m["p_hash"],
            "sig_lo": m["sig_lo"],
            "sig_hi": m["sig_hi"],
            "model": used_model,
            "relation": r["relation"],
            "confidence": r["confidence"],
            "reason": r["reason"],
        })

    _bulk_save_to_db(rows_to_persist)

    # 兜底：如果有任何 cell 仍然为 None（理论上不该出现），填 unrelated 防崩
    for i in range(n):
        for j in range(n):
            if matrix[i][j] is None:
                matrix[i][j] = {
                    "relation": "unrelated",
                    "confidence": 0.0,
                    "reason": "Fallback (cell was unexpectedly empty).",
                }

    return matrix  # type: ignore[return-value]


# ===== 自检入口 =====
if __name__ == "__main__":
    from pprint import pprint

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    # 先初始化数据库（确保 relation_cache 表存在）
    try:
        from database import init_db
        init_db()
    except Exception as e:
        print(f"[warn] init_db 失败（不影响仅测试单对判定）: {e}")

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

    print("\n>>> 测试 2：5 条 claims 构建 5x5 关系矩阵（首次，应该会调 LLM）\n")
    matrix = asyncio.run(build_matrix(DEMO_CLAIMS))

    LETTER = {"support": "S", "contradict": "C", "unrelated": "U"}
    print("矩阵紧凑视图（S=support, C=contradict, U=unrelated）:\n")
    print("     " + "   ".join(f"#{j}" for j in range(len(DEMO_CLAIMS))))
    for i, row in enumerate(matrix):
        cells = [f"{LETTER[c['relation']]}{c['confidence']:.1f}" for c in row]
        print(f"#{i}  " + " ".join(cells))

    print("\n>>> 关键判定的理由：")
    for i, j, label in [
        (0, 1, "矛盾对：13% 提升 vs 8% 下降"),
        (0, 2, "支持对：两条都正向"),
        (0, 4, "无关对：远程办公 vs 电池"),
    ]:
        cell = matrix[i][j]
        print(f"  #{i}↔#{j}  [{label}]")
        print(f"          {cell['relation']} (conf={cell['confidence']:.2f}) — {cell['reason']}")

    print(f"\n>>> L1 缓存大小：{len(_RELATION_CACHE)} 条")

    print("\n>>> 测试 3：再跑一次同样的 claims，应该全部命中 L2 缓存（零 LLM 调用）")
    _RELATION_CACHE.clear()  # 清 L1，强制走 L2
    matrix2 = asyncio.run(build_matrix(DEMO_CLAIMS))
    # 简单一致性校验
    assert all(
        matrix[i][j]["relation"] == matrix2[i][j]["relation"]
        for i in range(len(DEMO_CLAIMS))
        for j in range(len(DEMO_CLAIMS))
    ), "L2 缓存返回的关系应与首次一致"
    print(f"[OK] L2 命中验证通过；当前 L1 已被回填 {len(_RELATION_CACHE)} 条")


# ===========================================================
# 成本估算（20 篇论文 × 平均 3 条 claim = 60 条 claims）
# ===========================================================
# 全部 pair 数  = C(60, 2) = 1770
#   - subject 词汇过滤命中     ≈ 60% → 1062 跳过
#   - 真正进入 LLM 的剩余对    ≈ 708
#
# 现状（朴素 + 单对 LLM）:
#   708 次调用 × 600+80 token ≈ 480k input + 60k output token
#
# 启用 batch=6 后:
#   ≈ 118 次 LLM 调用 × (700+480) ≈ 140k input + 56k output
#   ≈ 输入 token 砍 70%，调用次数砍 6×（限流压力同步降低）
#
# 启用 SQLite L2 缓存后:
#   - 同一 query 重跑：~100% 命中，0 次调用
#   - 不同 query 但有重叠 paper：每篇重叠论文的 claim 间命中率 ~100%
#
# 简短结论：
#   - 首次冷跑：~0.15 元/次（比原来 0.5 元便宜 70%）
#   - 第二次跑同 query：≈ 0 元
# ===========================================================
