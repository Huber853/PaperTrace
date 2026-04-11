"""
PaperTrace - 切片 9：自动综述生成
=====================================

作用：拿着已经构建好的 claims + 矛盾矩阵，让 LLM 反向生成一段
      "学术综述风格"的中文段落——指出哪些论文分歧、哪些一致、
      并尝试分析分歧原因。

这是答辩的另一个大亮点。整个项目的核心价值被这一步浓缩：
    原始摘要 → 结构化 claims → 关系矩阵 → 回流成自然语言综述

换句话说：
    把一堆文字"压缩"成结构化数据做分析（切片 4/5）
    再把结构化数据"解压"成流畅综述（切片 9）

----------------------------------------------------
为什么不能直接让 LLM 读 20 篇摘要写综述？
----------------------------------------------------
1. 20 篇摘要的 token 量很大，一次塞进去成本高
2. LLM 自己没有"矛盾标记"，容易漏掉关键分歧点
3. 无法保证引用编号准确（[1][2] 这种）

我们的做法：
- 先通过切片 4/5 把分析工作做完（已知哪些对 contradict、哪些对 support）
- 把"预先识别的矛盾对"作为 prompt 的一部分显式喂给 LLM
- LLM 只需要"把已知事实编织成段落"，而不是"自己发现分歧"
- 成本低、引用准确、矛盾无遗漏
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv


# ===== 环境变量 =====
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

if not DEEPSEEK_API_KEY:
    raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量")


# ===== 高质量 prompt 模板（答辩亮点）=====
# 设计要点：
# 1. 用中文写，输出也是中文（符合答辩场景）
# 2. 明确段落长度（200-400 字）
# 3. 明确引用格式（[1][2][3]）
# 4. 显式告诉模型"哪些对是矛盾的、哪些对是支持的" —— 降低任务难度
# 5. 要求"尝试分析分歧原因"，给出 3 个维度提示（样本、方法、年代）
# 6. 给 2 个风格示范段落（一个分歧主题 + 一个共识主题），让模型模仿笔法
# 7. 禁止杜撰：只能基于给定的 claims 和关系
SYSTEM_PROMPT = """你是一位严谨的学术综述写手，专门撰写"研究现状 + 争议分析"类型的综述段落。

你的任务：基于给定的研究问题、论文列表、已抽取的主张、以及预先计算好的主张关系，
生成一段 200-400 字的学术风格中文综述段落。

你的段落必须满足：
1. 字数：200-400 个汉字（不含标点）
2. 引用格式：每次提到某项研究用 [N] 标注，N 是该论文在"论文列表"中的序号
3. 结构：
   - 先一句话总述该研究领域的整体态势（共识多还是分歧多）
   - 按主题归纳研究发现，明确指出哪些论文之间存在分歧（使用 [X] 与 [Y]）
   - 若存在分歧，从以下维度中挑 1-2 个尝试解释原因：样本群体、研究方法、观测周期、年代变迁
   - 最后一句给出审慎的结论（避免夸大，承认不确定性）
4. 风格：客观、冷静、学术，避免"令人震惊""显著"等口语化词汇
5. 铁律：只能基于给定的主张和关系做归纳，不得编造任何未在输入中出现的事实或作者观点

=========
风格示范 1（分歧主题）：
关于远程办公对生产力的影响，现有研究呈现明显分歧。[1] 的纵向调查发现全远程办公使软件工程师的自评生产力提升 13%，这一结论在 [2] 对知识工作者的测量中得到支持（产出指标提升 17%）。然而 [3] 在六个月追踪中观察到任务完成率下降 8%，与前两项研究形成直接对立。造成分歧的可能原因包括评价方式差异（自评 vs 实测）、样本行业不同，以及观察周期长短的影响——前两项属于短期报告，后者则反映中期效应。总体而言，远程办公的生产力效应高度依赖情境，不宜一概而论。

风格示范 2（共识主题）：
睡眠不足对认知功能的负面影响在多项研究中得到一致支持。[4] 发现大学生经历 24 小时急性睡眠剥夺后工作记忆显著下降，[5] 在成年人群中进一步观察到慢性睡眠限制使反应时间减慢 18%。尽管两项研究在人群（学生 vs 成人）与干预模式（急性 vs 慢性）上存在差异，但结论方向一致，构成相互补充的证据链。现有证据支持"充足睡眠是认知表现的必要条件"这一判断，但尚缺乏针对不同年龄段剂量-效应关系的系统研究。

=========

输出要求：直接输出综述段落，不要任何前后缀、标题或说明文字。不要输出 markdown，不要加引号。"""


# ===== 辅助：把矩阵里的关键关系对抽出来作为 prompt 上下文 =====
def _format_context(
    query: str,
    papers: list[dict],
    claims: list[dict],
    matrix: list[list[dict]],
) -> str:
    """
    构造给 LLM 的用户消息。
    我们把"哪些 claim 对矛盾、哪些支持"显式列出来，LLM 只需要编织段落。
    """

    # 1. 论文列表（引用编号用）
    paper_lines: list[str] = []
    # paper_id_to_index: 数据库 id → 在 prompt 里的 [N] 编号
    paper_id_to_index: dict[int, int] = {}
    for idx, p in enumerate(papers, start=1):
        paper_id_to_index[p["id"]] = idx
        year_part = f", {p['year']}" if p.get("year") else ""
        authors_part = ", ".join(p.get("authors", [])[:3])
        if len(p.get("authors", [])) > 3:
            authors_part += " 等"
        paper_lines.append(
            f"[{idx}] {p['title']} ({authors_part}{year_part})"
        )

    # 2. 主张列表（每条标出所属论文编号）
    claim_lines: list[str] = []
    for i, c in enumerate(claims):
        paper_num = paper_id_to_index.get(c["paper_id"], "?")
        claim_lines.append(
            f"  C{i} (属于 [{paper_num}]): "
            f"subject={c['subject']}; "
            f"intervention={c['intervention']}; "
            f"conclusion={c['conclusion']}; "
            f"direction={c['direction']}"
        )

    # 3. 从矩阵中提取矛盾对 / 支持对（只看上三角）
    contradict_pairs: list[str] = []
    support_pairs: list[str] = []
    n = len(claims)
    for i in range(n):
        for j in range(i + 1, n):
            cell = matrix[i][j]
            if cell["relation"] == "contradict":
                contradict_pairs.append(
                    f"  C{i} ↔ C{j} (置信度 {cell['confidence']:.2f}): {cell['reason']}"
                )
            elif cell["relation"] == "support":
                support_pairs.append(
                    f"  C{i} ↔ C{j} (置信度 {cell['confidence']:.2f}): {cell['reason']}"
                )

    # 4. 组装
    contradict_block = "\n".join(contradict_pairs) if contradict_pairs else "  （无明显矛盾）"
    support_block = "\n".join(support_pairs) if support_pairs else "  （无明显支持关系）"

    return f"""研究问题：{query}

论文列表（引用编号）：
{chr(10).join(paper_lines)}

抽取出的主张：
{chr(10).join(claim_lines)}

已识别的矛盾对（必须在综述中明确指出）：
{contradict_block}

已识别的支持对（可选择性提及以展示共识）：
{support_block}

请基于上述信息，生成一段 200-400 字的学术风格中文综述段落。"""


# ===== 核心函数 =====
async def generate_review(
    claims: list[dict],
    matrix: list[list[dict]],
    query: str,
    papers: list[dict],
) -> str:
    """
    生成综述段落。

    参数:
        claims: 主张列表，每个 dict 含 subject/intervention/conclusion/direction/paper_id
        matrix: N×N 关系矩阵
        query:  原始研究问题
        papers: 论文列表，每个 dict 至少含 id/title/authors/year

    返回:
        一段 200-400 字的中文综述段落字符串
    """
    if not claims:
        return "暂无可综述的主张内容。"

    user_msg = _format_context(query, papers, claims, matrix)

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        # 综述需要一点点文笔变化，温度比抽取/判定略高
        "temperature": 0.5,
        # 综述是纯文本不是 JSON，不需要 response_format
    }

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    url = f"{DEEPSEEK_BASE_URL}/chat/completions"

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    try:
        content: str = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"DeepSeek 返回结构异常: {data}") from e

    # 去掉模型偶尔加的前后空白和意外的 markdown 包裹
    content = content.strip()
    if content.startswith("```"):
        # 剥掉 ```xxx ... ``` 的代码块外壳
        lines = content.split("\n")
        if len(lines) >= 3:
            content = "\n".join(lines[1:-1]).strip()

    return content


# ===== 自检入口 =====
if __name__ == "__main__":
    import asyncio

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    # Mock 数据：3 篇论文 + 3 条 claim + 1 对矛盾
    demo_papers = [
        {
            "id": 1,
            "title": "Longitudinal Remote Work Productivity Study",
            "authors": ["A. Smith", "B. Lee"],
            "year": 2022,
        },
        {
            "id": 2,
            "title": "Knowledge Worker Output During Pandemic",
            "authors": ["C. Chen"],
            "year": 2023,
        },
        {
            "id": 3,
            "title": "Six-Month Tracking of Remote Engineering Teams",
            "authors": ["D. Wang", "E. Park", "F. Gupta"],
            "year": 2024,
        },
    ]
    demo_claims = [
        {
            "paper_id": 1,
            "subject": "software engineers",
            "intervention": "fully remote work",
            "conclusion": "self-rated productivity increased by 13%",
            "direction": "positive",
        },
        {
            "paper_id": 2,
            "subject": "knowledge workers",
            "intervention": "remote work arrangements",
            "conclusion": "output measures improved by 17% vs baseline",
            "direction": "positive",
        },
        {
            "paper_id": 3,
            "subject": "software engineers",
            "intervention": "fully remote work",
            "conclusion": "task completion rates dropped 8% over 6 months",
            "direction": "negative",
        },
    ]
    # 对应矩阵：0-1 support, 0-2 contradict, 1-2 contradict
    S = {"relation": "support", "confidence": 1.0, "reason": "self"}
    SUP = {"relation": "support", "confidence": 0.85, "reason": "both positive"}
    CON = {"relation": "contradict", "confidence": 0.92, "reason": "opposite productivity effects"}
    demo_matrix = [
        [S,   SUP, CON],
        [SUP, S,   CON],
        [CON, CON, S],
    ]

    print(">>> 调用 generate_review ...\n")
    review = asyncio.run(
        generate_review(
            claims=demo_claims,
            matrix=demo_matrix,
            query="远程办公对生产力的影响",
            papers=demo_papers,
        )
    )
    print("=" * 50)
    print(review)
    print("=" * 50)
    print(f"\n字数（含标点）：{len(review)}")
