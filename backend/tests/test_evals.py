from __future__ import annotations

from evals.metrics import citation_coverage, relation_accuracy, required_sections_score
from evals.runner import run_suite


def test_eval_metrics_are_deterministic():
    assert relation_accuracy(
        ["support", "contradict", "unrelated"],
        ["support", "unrelated", "unrelated"],
    ) == 2 / 3
    assert citation_coverage("结论得到[1]和[3]支持。", paper_count=3) == 2 / 3
    assert required_sections_score(
        {
            "query": "remote work",
            "papers": [],
            "claims": [],
            "matrix": [],
            "recommendations": {"questions": [], "methods": []},
        }
    ) == 1.0


def test_offline_smoke_suite_runs_without_model_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    report = run_suite("smoke", output_dir=tmp_path, live=False)

    assert report["passed"] is True
    assert report["cases"][0]["status"] == "completed"
    assert report["cases"][0]["phase_count"] == 7
    assert (tmp_path / "smoke.json").exists()
    assert (tmp_path / "smoke.md").exists()
