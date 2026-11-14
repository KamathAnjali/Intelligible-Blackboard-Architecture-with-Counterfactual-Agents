"""The allowlist helper only prints a review candidate; it never mutates policy."""

import json
from pathlib import Path

import pytest

from bench.kramabench_runner import COMPATIBILITY_ALLOWLIST_PATH
from bench.propose_compat_allowlist_entry import propose_entry


def test_candidate_entry_matches_pinned_task_without_editing_allowlist():
    benchmark_root = Path("/private/tmp/krama-phase2_1-authoritative")
    required = (
        benchmark_root / "evaluate.py",
        benchmark_root / "workload" / "environment.json",
        benchmark_root / "data" / "environment" / "input",
    )
    if not benchmark_root.is_dir() or not all(path.exists() for path in required):
        pytest.skip("authoritative pinned KramaBench checkout is unavailable")
    before = COMPATIBILITY_ALLOWLIST_PATH.read_bytes()

    candidate = propose_entry(benchmark_root, "environment", "environment-easy-1")

    approved = json.loads(before)["entries"][0]
    assert candidate == approved
    assert COMPATIBILITY_ALLOWLIST_PATH.read_bytes() == before
