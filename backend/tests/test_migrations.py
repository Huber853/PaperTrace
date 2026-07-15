from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_alembic_upgrade_head_creates_complete_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "papers",
        "claims",
        "contradictions",
        "relation_cache",
        "agent_runs",
        "agent_steps",
        "agent_tool_calls",
        "agent_artifacts",
        "agent_events",
        "alembic_version",
    } <= tables
