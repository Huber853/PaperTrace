"""
PaperTrace - 切片 4：从论文摘要中抽取"主张三元组"
====================================================

作用：用 DeepSeek API 把一段英文论文摘要转成结构化的"主张列表"。
每条主张包含 4 个字段：
    - subject       研究对象（who/what 被研究）
    - intervention  干预/变量（做了什么、对比了什么）
    - conclusion    结论描述（一句话总结发现）
    - direction     方向：positive / negative / neutral

被谁调用：
    - 切片 6 的 FastAPI /api/analyze 拉到论文 → 调 extract_claims(abstract) → 写入 claims 表

----------------------------------------------------
为什么要做"主张抽取"？
----------------------------------------------------
论文摘要是一段自然语言，机器没法直接拿来比对"两篇论文是不是矛盾"。
我们先把每篇摘要拆成几条标准化的"主张"，
然后切片 5 才能两两判断这些主张之间是支持还是矛盾。

----------------------------------------------------
为什么要用 Pydantic 校验？
----------------------------------------------------
LLM 是个"概率机器"：你让它返回 JSON，它 99% 的时候听话，但偶尔会：
    - 多塞个字段
    - 把 direction 写成 "POSITIVE" 而不是 "positive"
    - direction 写成 "increase"（不在我们预设的三个值里）
    - 数组里缺字段

Pydantic 在数据进入数据库前做一道"质检"，不合规的直接拦下来。
这就是 LLM 应用工程化的关键：永远不要相信 LLM 的输出格式。
"""

# ===== 导入区 =====
from __future__ import annotations  # 让类型提示用更新的写法（list[X] 而非 List[X]）

import json                         # 解析 LLM 返回的 JSON 字符串
import os                           # 读环境变量
import sys                          # Windows 控制台编码修正
from pathlib import Path            # 找 .env 文件位置
from typing import Literal          # 限制 direction 只能是预设的三个字符串

import httpx                        # 异步 HTTP 客户端，调 DeepSeek
from dotenv import load_dotenv      # 读 .env 文件到环境变量
from pydantic import BaseModel, Field, ValidationError, field_validator


# ===== 加载环境变量 =====
# 找当前文件所在目录下的 .env，加载到 os.environ
# 不指定路径的话，load_dotenv() 默认从当前工作目录找，容易踩坑
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# 启动时就检查 key 在不在，缺了直接报错，避免运行时再炸
if not DEEPSEEK_API_KEY:
    raise RuntimeError(
        "缺少 DEEPSEEK_API_KEY 环境变量。"
        "请把 .env.example 复制成 .env 并填上你的 key。"
    )


# ===== Pydantic 模型：定义每条主张的"标准形状" =====
class Claim(BaseModel):
    """一条主张的结构化表示。LLM 返回的每个 dict 都会用这个模型校验。"""

    subject: str = Field(..., min_length=1, max_length=500, description="研究对象")
    intervention: str = Field(..., min_length=1, max_length=500, description="干预/变量")
    conclusion: str = Field(..., min_length=1, max_length=2000, description="结论描述")
    direction: Literal["positive", "negative", "neutral"] = Field(
        ..., description="方向：positive/negative/neutral"
    )

    # 自定义校验器：把 LLM 偶尔大写的 "POSITIVE" 自动小写化，多一道宽容
    @field_validator("direction", mode="before")
    @classmethod
    def _lowercase_direction(cls, v):
        if isinstance(v, str):
            v = v.strip().lower()
            # 容忍一些常见的同义词
            mapping = {
                "increase": "positive", "improve": "positive", "improved": "positive",
                "decrease": "negative", "decline": "negative", "reduced": "negative",
                "no effect": "neutral", "mixed": "neutral", "none": "neutral",
            }
            v = mapping.get(v, v)
        return v


class ClaimsResponse(BaseModel):
    """LLM 返回的整个 JSON 对象的结构。最外层是 {"claims": [...]}。"""
    claims: list[Claim] = Field(default_factory=list)


# ===== 高质量 system prompt =====
# 设计要点：
# 1. 用英文写，因为论文摘要多半是英文，模型在英文域内更稳
# 2. 明确给出"只输出 JSON、不带 markdown 代码块"的硬约束
# 3. 给一个 few-shot 示例，让模型有样可学
# 4. 限定 direction 的三个值，并解释每个值什么时候用
# 5. 强调"如果摘要里没有可抽取的主张，就返回空数组"——避免模型瞎编
SYSTEM_PROMPT = """You are a scientific claim extraction engine for academic abstracts.

Your job: read a paper abstract and output a list of structured "claims" found in it.

Each claim has 4 fields:
- subject:      the population, system, or thing being studied (e.g., "remote software engineers", "lithium-ion batteries")
- intervention: the variable, treatment, or comparison (e.g., "fully remote work", "fast-charging at 4C")
- conclusion:   a one-sentence description of what was found (e.g., "productivity increased by 13%")
- direction:    exactly one of: "positive" | "negative" | "neutral"
    * "positive" = the intervention had a beneficial / increasing / supporting effect on the subject
    * "negative" = the intervention had a harmful / decreasing / opposing effect
    * "neutral"  = no significant effect, mixed results, or descriptive findings without a clear direction

OUTPUT RULES — read carefully:
1. Output ONLY a JSON object. No markdown, no ```json fences, no prose, no explanations.
2. The JSON must have exactly one top-level key: "claims", whose value is an array of claim objects.
3. If the abstract contains no extractable claims, return {"claims": []}.
4. Extract 1 to 5 claims per abstract — the most important findings only. Do not invent claims.
5. Keep each field concise (under 30 words). Use the abstract's own wording when possible.

EXAMPLE INPUT:
"We surveyed 500 software engineers during the COVID-19 pandemic. Fully remote work was associated with a 13% increase in self-reported productivity, but a 22% increase in feelings of isolation. Hybrid arrangements showed no significant change in either metric."

EXAMPLE OUTPUT:
{"claims":[{"subject":"software engineers","intervention":"fully remote work","conclusion":"self-reported productivity increased by 13%","direction":"positive"},{"subject":"software engineers","intervention":"fully remote work","conclusion":"feelings of isolation increased by 22%","direction":"negative"},{"subject":"software engineers","intervention":"hybrid work arrangements","conclusion":"no significant change in productivity or isolation","direction":"neutral"}]}
"""


# ===== 核心函数 =====
async def extract_claims(abstract: str) -> list[dict]:
    """
    从一段英文论文摘要中抽取主张列表。

    参数:
        abstract: 论文摘要原文（建议英文，模型在英文上更稳）

    返回:
        list[dict]，每个 dict 包含 subject/intervention/conclusion/direction 四个键。
        如果抽不到任何主张或全部校验失败，返回空列表（不会抛异常）。

    异常:
        - 网络错误、API 4xx/5xx 会向上抛
        - JSON 解析失败会自动重试一次；两次都失败返回空列表
    """

    # 构造 OpenAI 兼容的 chat completions 请求体
    # DeepSeek API 完全兼容 OpenAI 协议，response_format 也支持 json_object 模式
    # json_object 模式会让模型输出强制为合法 JSON（双重保险，配合 prompt 用）
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Abstract:\n{abstract.strip()}"},
        ],
        "temperature": 0.1,                # 低温度 → 输出更稳定、不发散
        "response_format": {"type": "json_object"},  # 强制 JSON 输出
    }

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    url = f"{DEEPSEEK_BASE_URL}/chat/completions"

    # ===== 调 LLM，最多两次（首次 + 一次解析重试）=====
    last_error: str | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in (1, 2):
            try:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()  # 4xx/5xx 直接抛
            except httpx.HTTPStatusError as e:
                # API 调用本身失败：打印细节方便定位（401=key 错；402=没钱；429=限流）
                print(f"[extractor] DeepSeek HTTP {e.response.status_code}: {e.response.text[:200]}")
                raise

            data = response.json()
            # OpenAI 协议返回结构：{"choices":[{"message":{"content":"..."}}], ...}
            try:
                content: str = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                raise RuntimeError(f"DeepSeek 返回结构异常：{data}") from e

            # ===== 解析 + 校验 =====
            try:
                # 第一道：JSON 解析
                raw = json.loads(content)
                # 第二道：Pydantic 结构校验（最外层 + 每条 claim）
                validated = ClaimsResponse.model_validate(raw)
                # 通过校验：转回 dict 列表返回
                return [c.model_dump() for c in validated.claims]

            except (json.JSONDecodeError, ValidationError) as e:
                last_error = f"{type(e).__name__}: {e}"
                print(f"[extractor] 第 {attempt}/2 次解析失败：{last_error}")
                if attempt == 1:
                    # 重试时往对话里补一条更严厉的提醒
                    payload["messages"].append({"role": "assistant", "content": content})
                    payload["messages"].append({
                        "role": "user",
                        "content": (
                            "Your previous response was not valid. "
                            "Return ONLY a JSON object with key 'claims', "
                            "no markdown, no extra text. Try again."
                        ),
                    })
                    continue
                # 第二次还失败，放弃返回空列表（不让上游崩）
                print("[extractor] 两次都失败，返回空列表。")
                return []

    return []  # 理论上到不了这里


# ===== 自检入口 =====
if __name__ == "__main__":
    import asyncio
    from pprint import pprint

    # Windows 控制台中文修正
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    # 一段真实风格的英文摘要（混合发现，正负中三种方向都有）
    DEMO_ABSTRACT = (
        "We conducted a longitudinal study of 1,200 knowledge workers across "
        "12 multinational companies between 2020 and 2023 to evaluate the "
        "long-term effects of remote work arrangements. Fully remote employees "
        "reported a 17% increase in self-rated productivity compared to their "
        "in-office baseline, with the largest gains observed among engineers "
        "and writers. However, the same group experienced a 24% rise in "
        "feelings of professional isolation and a measurable decline in "
        "cross-team collaboration metrics. Hybrid workers (2-3 days remote) "
        "showed no statistically significant change in either productivity "
        "or collaboration. We conclude that remote work is not uniformly "
        "beneficial and that organizational support structures matter more "
        "than the remote/in-office binary."
    )

    print(">>> 测试摘要：\n")
    print(DEMO_ABSTRACT)
    print("\n>>> 调用 DeepSeek 抽取主张 ...\n")

    claims = asyncio.run(extract_claims(DEMO_ABSTRACT))

    print(f">>> 抽到 {len(claims)} 条主张：\n")
    for i, c in enumerate(claims, start=1):
        print(f"[{i}] [{c['direction']}] {c['subject']}")
        print(f"    干预：{c['intervention']}")
        print(f"    结论：{c['conclusion']}\n")

    print(">>> 完整结构：")
    pprint(claims)


# ===========================================================
# 如何运行
# ===========================================================
# 1. 先确保 backend/.env 里有 DEEPSEEK_API_KEY=sk-xxxxx
# 2. 激活 venv：    source venv/Scripts/activate
# 3. 在 backend：   python extractor.py
#
# 你会看到 3 条主张被抽出来：
#   [1] [positive] knowledge workers / remote work / 生产力 +17%
#   [2] [negative] knowledge workers / remote work / 孤独感 +24%
#   [3] [neutral]  knowledge workers / hybrid work / 无显著变化
#
# 常见报错：
#   - RuntimeError: 缺少 DEEPSEEK_API_KEY
#       → .env 文件没建或没填 key
#   - HTTP 401 Unauthorized
#       → key 错了或被吊销
#   - HTTP 402 Payment Required
#       → DeepSeek 账户余额不足，去充值（一般几块钱够测试很久）
#   - HTTP 429
#       → 请求太频繁，等几秒再试
# ===========================================================
