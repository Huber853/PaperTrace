from __future__ import annotations

import re
from typing import Any


def relation_accuracy(expected: list[str], predicted: list[str]) -> float:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted relation lists must have equal length")
    if not expected:
        return 1.0
    return sum(left == right for left, right in zip(expected, predicted)) / len(expected)


def citation_coverage(review: str, paper_count: int) -> float:
    if paper_count <= 0:
        return 1.0
    cited = {
        int(value)
        for value in re.findall(r"\[(\d+)\]", review)
        if 1 <= int(value) <= paper_count
    }
    return len(cited) / paper_count


def required_sections_score(report: dict[str, Any]) -> float:
    required = ("query", "papers", "claims", "matrix", "recommendations")
    return sum(key in report for key in required) / len(required)
