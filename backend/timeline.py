"""
PaperTrace - 观点演化时间轴
============================

把主张按年份聚合,输出每一年各立场(positive / negative / neutral)的数量。
前端用这些数据画堆叠面积图和散点图,帮用户一眼看出"观点反转点"。

为什么放在独立模块?
  timeline 逻辑纯粹是数据转换,不涉及网络请求、数据库、LLM 调用,
  单独拆出来方便测试,也避免 main.py 越来越长。

输入说明:
  claims 列表里每条需要有 direction 字段。
  year 不在 claim 上,而在 paper 上,所以调用方需要先通过
  paper_id 把 year 查出来,以 {claim_id: year} 映射传进来。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional


def build_timeline(
    claims: list[dict],
    paper_year_map: dict[int, Optional[int]],
) -> list[dict]:
    """
    按年份聚合主张的立场分布。

    参数:
        claims: 主张列表,每条至少包含 {"id": int, "paper_id": int, "direction": str}
        paper_year_map: {paper.id: paper.year} 映射,用来给每条 claim 补上年份

    返回:
        按 year 升序排列的列表,每条形如:
        {
            "year": 2020,
            "positive": 5,
            "negative": 2,
            "neutral": 1,
            "total": 8,
        }
        year 为 None 的主张会被跳过(有的论文没有发表年份)。
    """
    # 按年份分桶,统计三种立场的数量
    buckets: dict[int, dict[str, int]] = defaultdict(
        lambda: {"positive": 0, "negative": 0, "neutral": 0}
    )

    for c in claims:
        year = paper_year_map.get(c["paper_id"])
        if year is None:
            continue
        direction = c.get("direction", "neutral")
        # 安全兜底:direction 不在三种之一就当 neutral
        if direction not in ("positive", "negative", "neutral"):
            direction = "neutral"
        buckets[year][direction] += 1

    # 转成列表并按年份升序排列
    result = []
    for year in sorted(buckets):
        b = buckets[year]
        total = b["positive"] + b["negative"] + b["neutral"]
        result.append({
            "year": year,
            "positive": b["positive"],
            "negative": b["negative"],
            "neutral": b["neutral"],
            "total": total,
        })

    return result


# ===== 直接运行时的自检 =====
if __name__ == "__main__":
    fake_claims = [
        {"id": 1, "paper_id": 10, "direction": "positive"},
        {"id": 2, "paper_id": 11, "direction": "positive"},
        {"id": 3, "paper_id": 12, "direction": "negative"},
        {"id": 4, "paper_id": 13, "direction": "negative"},
        {"id": 5, "paper_id": 13, "direction": "negative"},
        {"id": 6, "paper_id": 14, "direction": "neutral"},
        {"id": 7, "paper_id": 15, "direction": "positive"},
    ]
    fake_year_map = {
        10: 2018, 11: 2018,
        12: 2020, 13: 2020,
        14: 2022,
        15: None,  # 没有年份 → 跳过
    }

    tl = build_timeline(fake_claims, fake_year_map)
    print("时间轴:", tl)

    assert len(tl) == 3  # 2018, 2020, 2022
    assert tl[0] == {"year": 2018, "positive": 2, "negative": 0, "neutral": 0, "total": 2}
    assert tl[1] == {"year": 2020, "positive": 0, "negative": 3, "neutral": 0, "total": 3}
    assert tl[2] == {"year": 2022, "positive": 0, "negative": 0, "neutral": 1, "total": 1}
    print("✅ 自检通过")
