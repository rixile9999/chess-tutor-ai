"""The test environment itself: the CI workflow really runs the engine pipeline, and the engine
is pinned to one thread so multipv ranks below the best move are reproducible.

Both are invisible from the outside. A CI job without Stockfish still goes green, it just skips
test_e2e, test_engine and the engine half of analysis/maia/review/reasoning; a two-thread engine
still answers, it just answers differently from run to run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from chess_tutor.config import get_settings

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    assert WORKFLOW.is_file(), f"missing {WORKFLOW}"
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _run_steps(job: dict[str, Any]) -> list[str]:
    return [str(step["run"]) for step in job["steps"] if "run" in step]


def test_ci_installs_stockfish_and_points_the_suite_at_it(workflow: dict[str, Any]) -> None:
    api = workflow["jobs"]["api"]
    steps = _run_steps(api)

    # The official AVX2 release binary, not the apt package: the Debian build is a generic
    # x86-64 binary whose NNUE evaluation is several times slower, and the suite's depth-14
    # confirmation searches then exceed the job timeout.
    installs = [s for s in steps if "stockfish" in s and "releases/download" in s]
    assert installs, f"the api job never downloads stockfish: {steps}"

    # engine.find_stockfish only sees the binary through STOCKFISH_PATH, so the install step
    # must put it exactly there.
    path = api.get("env", {}).get("STOCKFISH_PATH")
    assert isinstance(path, str) and path.startswith("/"), api.get("env")
    assert "$STOCKFISH_PATH" in installs[0] or path in installs[0], installs[0]

    pytest_steps = [s for s in steps if "pytest" in s]
    assert pytest_steps, steps
    assert steps.index(installs[0]) < steps.index(pytest_steps[0])


def test_both_ci_jobs_have_a_timeout(workflow: dict[str, Any]) -> None:
    """Analysis waits up to 600s on the engine; a stuck job must not burn the runner's 6 hours."""
    for name, job in workflow["jobs"].items():
        timeout = job.get("timeout-minutes")
        assert isinstance(timeout, int) and 0 < timeout <= 60, f"{name}: {timeout!r}"


def test_engine_runs_single_threaded_under_test() -> None:
    """Stockfish is only deterministic with one thread, and several tests read rank 2 or 3."""
    assert get_settings().engine_threads == 1
