"""Run the pinned compatibility evaluator end-to-end with fake model responses.

Invoke this script with the isolated KramaBench evaluator Python and provide
the authoritative checkout with --kramabench-root. It makes no model or API
requests and writes all fixture output under a fresh temporary directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import runpy
import sys
import tempfile
import types
import urllib.request
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.llm_client import OllamaClient
from bench.kramabench_adapter import KramaBenchSystem
from bench.kramabench_runner import (
    COMPATIBILITY_EVALUATOR_MODE,
    _prepare_compatibility_checkout,
    _run_provenance_metadata,
)
from bench.kramabench_preflight import PreflightSummary, PreflightTask


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kramabench-root", required=True)
    args = parser.parse_args()
    source_root = Path(args.kramabench_root).resolve(strict=True)

    # This subprocess is deliberately keyless even if its caller has other
    # environment state. The compatibility path never needs OpenAI credentials.
    os.environ.pop("OPENAI_API_KEY", None)

    network_attempts: list[str] = []

    def deny_network(*_args, **_kwargs):
        network_attempts.append("blocked")
        raise AssertionError("network access is disabled in this synthetic integration fixture")

    httpx.Client.send = deny_network
    httpx.AsyncClient.send = deny_network
    urllib.request.urlopen = deny_network

    with tempfile.TemporaryDirectory(prefix="kramabench-compat-fixture-") as temporary:
        temporary_root = Path(temporary)
        compatibility_root = temporary_root / "evaluator_compatibility_no_key"
        compatibility = _prepare_compatibility_checkout(source_root, compatibility_root)
        sys.path.insert(0, str(compatibility_root))

        from benchmark.evaluator import Evaluator
        from benchmark.metrics import metric_factory

        run_root = temporary_root / "synthetic_run"
        (run_root / "workload").mkdir(parents=True)
        input_root = run_root / "data" / "fixture" / "input"
        input_root.mkdir(parents=True)
        (run_root / "benchmark").symlink_to(compatibility_root / "benchmark", target_is_directory=True)
        data_path = input_root / "records.csv"
        data_path.write_text("value\n42\n", encoding="utf-8")
        data_sha256 = hashlib.sha256(data_path.read_bytes()).hexdigest()
        selected_task = {
            "id": "fixture-1",
            "query": "Read records.csv and report the maximum value.",
            "answer": 42,
            "answer_type": "numeric_exact",
            "data_sources": ["records.csv"],
            "evaluator_private_marker": "EVALUATOR_ONLY_SECRET",
            "reference_pipeline": "REFERENCE_PIPELINE_SECRET",
        }
        task_path = run_root / "workload" / "fixture.json"
        task_path.write_text(json.dumps([selected_task], indent=2), encoding="utf-8")

        class FakeClient(OllamaClient):
            def __init__(self):
                self.model = "synthetic-fixture-model"
                self.base_url = "http://127.0.0.1:11434"
                self.prompts: list[str] = []
                self.responses = iter([
                    {"action": "aggregate", "file": "records.csv", "measures": [
                        {"function": "max", "column": "value", "name": "observed_value"}
                    ]},
                    {"action": "done", "summary": "The permitted CSV contains the requested maximum."},
                    {"tag": "REVISE", "prediction": "42", "explanation": "The permitted CSV has a value of 42."},
                    {"tag": "RATIFY", "prediction": "42", "explanation": "The answer follows from the CSV."},
                    {"tag": "RATIFY", "prediction": "42", "explanation": "The answer is supported by the evidence."},
                    {"pipeline_code": "import pandas as pd\ndf = pd.read_csv('records.csv')\nprint(df['value'].max())"},
                ])
                self.fake_generation_calls = 0

            def generate(self, prompt, system="", schema=None):
                self.fake_generation_calls += 1
                self.prompts.append(prompt)
                return {
                    "response": json.dumps(next(self.responses)),
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 11,
                    "eval_count": 5,
                    "wall_seconds": 0.01,
                }

            def check_explanation(self, task, answer):
                return {
                    "raw": {"prompt_eval_count": 2, "eval_count": 1},
                    "parsed": {"supported": True, "reason": "synthetic fixture"},
                    "error": None,
                }

        clients: list[FakeClient] = []

        class FixtureSystem(KramaBenchSystem):
            def __init__(self, verbose=False, output_dir=None):
                self.fake_client = FakeClient()
                clients.append(self.fake_client)
                super().__init__(
                    verbose=verbose,
                    output_dir=output_dir or run_root / "system_scratch" / "KramaBenchPXPSystem",
                    client=self.fake_client,
                    max_turns=4,
                    max_retries=0,
                    pipeline_evaluation_enabled=False,
                )

        FixtureSystem.__name__ = "KramaBenchPXPSystem"
        FixtureSystem.__qualname__ = "KramaBenchPXPSystem"
        systems_module = types.ModuleType("systems")
        systems_module.KramaBenchPXPSystem = FixtureSystem
        sys.modules["systems"] = systems_module

        evaluator = Evaluator(
            str(task_path),
            str(compatibility_root / "benchmark" / "fixtures"),
            str(run_root / "results"),
            evaluate_pipeline=False,
        )
        assert evaluator.pipeline_evaluation_engine is None
        sys.argv = [
            str(compatibility_root / "evaluate.py"),
            "--sut", "KramaBenchPXPSystem",
            "--workload", "fixture",
            "--project_root", str(run_root),
            "--result_directory", "results",
            "--task_fixtures", "benchmark/fixtures",
            "--num_workers", "1",
            "--use_truth_subset",
            "--no_pipeline_eval",
        ]
        runpy.run_path(str(compatibility_root / "evaluate.py"), run_name="__main__")

        measure_files = sorted((run_root / "results" / "KramaBenchPXPSystem").glob("fixture_measures_*.csv"))
        aggregate_path = run_root / "results" / "aggregated_results.csv"
        assert len(measure_files) == 1 and aggregate_path.is_file()
        with measure_files[0].open(newline="", encoding="utf-8") as handle:
            measures = list(csv.DictReader(handle))
        task_success = [row for row in measures if row.get("task_id") == "fixture-1" and row.get("metric") == "success"]
        assert len(task_success) == 1 and float(task_success[0]["value"]) == 1.0, measures
        with aggregate_path.open(newline="", encoding="utf-8") as handle:
            aggregate_rows = list(csv.DictReader(handle))
        assert any(row.get("metric") == "success" and float(row.get("value_mean", "nan")) == 1.0 for row in aggregate_rows)

        record_paths = sorted(
            (run_root / "system_scratch" / "KramaBenchPXPSystem" / "krama_task_records").glob("*.json")
        )
        assert len(record_paths) == 1, record_paths
        records_path = record_paths[0]
        record = json.loads(records_path.read_text(encoding="utf-8"))
        assert record["task_id"] == "fixture-1"
        assert record["status"] == "success"
        assert record["pipeline_code"] and "exec(" not in record["pipeline_code"]
        all_prompts = "\n".join(prompt for client in clients for prompt in client.prompts)
        for marker in ("EVALUATOR_ONLY_SECRET", "REFERENCE_PIPELINE_SECRET"):
            assert marker not in all_prompts
        assert len(clients) == 1 and clients[0].fake_generation_calls == 6
        assert network_attempts == []

        synthetic_summary = PreflightSummary(
            workload_path=str(task_path), dataset_directory=str(input_root), task_count=1,
            requested_task_ids=("fixture-1",),
            tasks=(PreflightTask(
                task_id="fixture-1", answer_type="numeric_exact",
                permitted_files=({"path": "records.csv", "sha256": data_sha256},),
            ),),
        )
        provenance = {
            "run_class": "synthetic_compatibility_fixture",
            "evaluator_mode": COMPATIBILITY_EVALUATOR_MODE,
            "benchmark_revision": compatibility["revision"],
            "compatibility_patch_sha256": compatibility["patch_sha256"],
            "patched_evaluator_sha256": compatibility["patched_evaluator_sha256"],
            "task_id": synthetic_summary.tasks[0].task_id,
            "answer_type": synthetic_summary.tasks[0].answer_type,
            "data_sha256": synthetic_summary.tasks[0].permitted_files[0]["sha256"],
            "result_label": "fixture-only; not a KramaBench benchmark result",
        }
        provenance_path = run_root / "synthetic_fixture_provenance.json"
        provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        assert provenance["evaluator_mode"] != "official_unmodified"
        assert provenance["task_id"] == "fixture-1"
        assert provenance["data_sha256"] == data_sha256

        # Sanity-check the same pinned deterministic metric directly.
        assert metric_factory("success")(predicted="42", target=42) == (1, 0, 0, 0)
        print("compatibility-no-key synthetic integration: PASS")
        print("adapter + PXP + pinned Executor/Evaluator + success metric + task/aggregate CSVs: PASS")
        print("compatibility patch SHA-256: " + compatibility["patch_sha256"])
        print("fake model outputs: 6; real Ollama generation requests: 0")
        print("OpenAI/network HTTP attempts: 0")
        print("reference answer and pipeline markers in agent prompts: absent")
        print("result classification: synthetic fixture only; no benchmark score")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
