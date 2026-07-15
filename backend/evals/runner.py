from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import agent.models  # noqa: F401
from agent.harness import AgentHarness
from agent.model_provider import DeepSeekModelProvider
from agent.repository import AgentRepository
from agent.schemas import RunStatus
from agent.tools import build_default_tool_registry
from database import Base
from .datasets import load_cases
from .fakes import build_offline_runtime
from .metrics import citation_coverage, relation_accuracy, required_sections_score


DEFAULT_REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def run_suite(
    suite: str,
    *,
    output_dir: str | Path = DEFAULT_REPORTS_DIR,
    live: bool = False,
) -> dict[str, Any]:
    cases = load_cases(suite)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if suite == "relations":
        results = [_run_relation_case(case) for case in cases]
    else:
        results = [_run_harness_case(case, live=live) for case in cases]

    report = {
        "suite": suite,
        "live": live,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(case["passed"] for case in results),
        "cases": results,
    }
    (output_path / f"{suite}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_path / f"{suite}.md").write_text(
        _markdown_report(report),
        encoding="utf-8",
    )
    return report


def _run_harness_case(case: dict[str, Any], *, live: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="papertrace-eval-") as temp_dir:
        engine = create_engine(
            f"sqlite:///{Path(temp_dir) / 'eval.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        repository = AgentRepository(factory)
        run = repository.create_run(
            query=case["query"],
            paper_limit=int(case.get("paper_limit", 5)),
        )
        if live:
            registry = build_default_tool_registry()
            provider = DeepSeekModelProvider()
        else:
            registry, provider = build_offline_runtime(case["query"])
        completed = asyncio.run(
            AgentHarness(repository, registry, provider).run(run.id)
        )
        final = repository.latest_artifact(run.id, "final_report")
        report = final.content_json if final else {}
        phases = {step.phase.value for step in repository.list_steps(run.id)}
        expected_status = case.get("expected_status", "completed")
        expected_artifacts = set(case.get("expected_artifacts", []))
        actual_artifacts = {item.kind for item in repository.list_artifacts(run.id)}
        tool_call_count = len(repository.list_tool_calls(run.id))
        passed = (
            completed.status.value == expected_status
            and expected_artifacts.issubset(actual_artifacts)
            and required_sections_score(report) == 1.0
        )
        engine.dispose()
        return {
            "name": case["name"],
            "status": completed.status.value,
            "phase_count": len(phases),
            "step_count": completed.step_count,
            "tool_call_count": tool_call_count,
            "artifact_count": len(actual_artifacts),
            "citation_coverage": citation_coverage(
                report.get("review", ""),
                len(report.get("papers", [])),
            ),
            "passed": passed,
        }


def _run_relation_case(case: dict[str, Any]) -> dict[str, Any]:
    score = relation_accuracy(case["expected"], case["predicted"])
    threshold = float(case.get("threshold", 1.0))
    return {
        "name": case["name"],
        "status": "completed",
        "accuracy": score,
        "threshold": threshold,
        "passed": score >= threshold,
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        f"# PaperTrace Eval: {report['suite']}",
        "",
        f"- Passed: {'yes' if report['passed'] else 'no'}",
        f"- Live: {'yes' if report['live'] else 'no'}",
        f"- Generated: {report['generated_at']}",
        "",
        "## Cases",
        "",
    ]
    for case in report["cases"]:
        lines.append(
            f"- {'PASS' if case['passed'] else 'FAIL'} `{case['name']}` ({case['status']})"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PaperTrace Agent evaluations")
    parser.add_argument("--suite", default="smoke", choices=("smoke", "relations"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default=str(DEFAULT_REPORTS_DIR))
    args = parser.parse_args()
    report = run_suite(args.suite, output_dir=args.output, live=args.live)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
