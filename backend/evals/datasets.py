from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CASES_DIR = Path(__file__).resolve().parent / "cases"


def load_cases(suite: str) -> list[dict[str, Any]]:
    path = CASES_DIR / f"{suite}.json"
    if not path.exists():
        raise ValueError(f"unknown eval suite: {suite}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"eval suite must be a JSON list: {path}")
    return payload
