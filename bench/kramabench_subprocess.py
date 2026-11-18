"""Bootstrap the configured project System inside an isolated evaluator Python."""

from __future__ import annotations

import json
import os
import runpy
import sys
import types
from pathlib import Path


def apply_effective_model_options(client, *, num_ctx: int, num_predict: int,
                                  required_num_ctx: int) -> dict[str, int]:
    """Set and verify the exact context/output options used in Ollama payloads."""
    if any(type(value) is not int or value <= 0
           for value in (num_ctx, num_predict, required_num_ctx)):
        raise RuntimeError("Ollama num_ctx, num_predict, and required context must be positive integers")
    effective = {**client.options, "num_ctx": num_ctx, "num_predict": num_predict}
    client.options = effective
    if effective["num_ctx"] < required_num_ctx:
        raise RuntimeError(
            f"effective Ollama context {effective['num_ctx']} is below frozen-scope requirement {required_num_ctx}"
        )
    if effective["num_predict"] > effective["num_ctx"]:
        raise RuntimeError("Ollama num_predict cannot exceed effective num_ctx")
    return dict(effective)

def main() -> None:
    if os.environ.get("KRAMABENCH_GENUINE_RUN_APPROVED") != "1":
        raise RuntimeError("isolated evaluator requires explicit genuine-run approval")
    project_root = Path(os.environ["KRAMABENCH_PROJECT_ROOT"]).resolve(strict=True)
    sys.path.insert(0, str(project_root))
    from bench.kramabench_runner import (
        COMPATIBILITY_EVALUATOR_MODE,
        COMPATIBILITY_EVALUATOR_PATH,
        COMPATIBILITY_PATCH_SHA256,
        AUDITED_DETERMINISTIC_METRICS,
        COMPATIBILITY_UNSAFE_METRICS,
        EVALUATOR_MODES,
        KRAMABENCH_REVISION,
        _compatibility_allowlist_entry,
        _verify_compatibility_checkout,
        verify_benchmark_checkout,
    )
    from bench.kramabench_preflight import WorkloadPreflightError

    evaluate_path = Path(os.environ["KRAMABENCH_EVALUATE_PATH"]).resolve(strict=True)
    run_root = Path(os.environ["KRAMABENCH_RUN_ROOT"]).resolve(strict=True)
    arguments = json.loads(os.environ["KRAMABENCH_EVALUATOR_ARGS"])
    evaluator_mode = os.environ.get("KRAMABENCH_EVALUATOR_MODE", "")
    if evaluator_mode not in EVALUATOR_MODES:
        raise RuntimeError("isolated evaluator requires a recognized evaluator mode")

    pipeline_evaluation = os.environ.get("KRAMABENCH_PIPELINE_EVAL", "0") == "1"
    if evaluator_mode == COMPATIBILITY_EVALUATOR_MODE:
        workload = os.environ.get("KRAMABENCH_WORKLOAD", "")
        task_id = os.environ.get("KRAMABENCH_TASK_ID", "")
        try:
            allowlist_entry = _compatibility_allowlist_entry(workload, task_id)
        except WorkloadPreflightError as exc:
            raise RuntimeError("compatibility-no-key task is not present in the project allowlist") from exc
        provenance = allowlist_entry.get("evaluator_provenance", {})
        if (
            pipeline_evaluation
            or "--no_pipeline_eval" not in arguments
            or "--run_subtasks" in arguments
            or os.environ.get("KRAMABENCH_COMPAT_PATCH_SHA256") != COMPATIBILITY_PATCH_SHA256
            or provenance.get("revision") != KRAMABENCH_REVISION
            or provenance.get("patch_path") != COMPATIBILITY_EVALUATOR_PATH
            or provenance.get("patch_sha256") != COMPATIBILITY_PATCH_SHA256
        ):
            raise RuntimeError("compatibility-no-key evaluator configuration is incomplete or unsupported")
        original_root = Path(os.environ["KRAMABENCH_BENCHMARK_ROOT"]).resolve(strict=True)
        verify_benchmark_checkout(original_root)
        try:
            answer_fixtures = json.loads(
                (original_root / "benchmark" / "fixtures" / "answer_type_fixtures.json").read_text(encoding="utf-8")
            )
            metric_names = next(
                item["metrics"] for item in answer_fixtures
                if item.get("name") == allowlist_entry.get("answer_type")
            )
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, StopIteration) as exc:
            raise RuntimeError("compatibility-no-key answer metric mapping cannot be verified") from exc
        if (
            metric_names != allowlist_entry.get("metric_names")
            or not isinstance(metric_names, list)
            or not metric_names
            or set(metric_names) - AUDITED_DETERMINISTIC_METRICS
            or set(metric_names) & COMPATIBILITY_UNSAFE_METRICS
        ):
            raise RuntimeError("compatibility-no-key answer metrics are not approved for deterministic evaluation")
        if evaluate_path.parent == original_root:
            raise RuntimeError("compatibility evaluator must use a separate source copy")
        try:
            evaluate_path.parent.relative_to(run_root)
        except ValueError as exc:
            raise RuntimeError("compatibility evaluator copy must be isolated inside the run directory") from exc
        patch_info = _verify_compatibility_checkout(evaluate_path.parent)
        if patch_info["patch_sha256"] != COMPATIBILITY_PATCH_SHA256:
            raise RuntimeError("compatibility evaluator patch does not match the reviewed patch")
        # The no-key path removes inherited credentials instead of inspecting them.
        os.environ.pop("OPENAI_API_KEY", None)

    from agents.llm_client import MODEL, BASE_URL, OllamaClient
    from bench.kramabench_adapter import KramaBenchSystem

    model = os.environ.get("KRAMABENCH_MODEL", MODEL)
    model_seed = int(os.environ.get("KRAMABENCH_MODEL_SEED", "42"))
    num_ctx = int(os.environ.get("KRAMABENCH_NUM_CTX", "4096"))
    num_predict = int(os.environ.get("KRAMABENCH_NUM_PREDICT", "768"))
    required_num_ctx = int(os.environ.get("KRAMABENCH_REQUIRED_NUM_CTX", str(num_ctx)))
    base_url = os.environ.get("KRAMABENCH_BASE_URL", BASE_URL)
    max_turns = int(os.environ.get("KRAMABENCH_MAX_TURNS", "6"))
    max_retries = int(os.environ.get("KRAMABENCH_MAX_RETRIES", "1"))
    task_wall_clock_cap_seconds = int(os.environ.get("KRAMABENCH_TASK_WALL_CLOCK_CAP_SECONDS", "900"))
    counterfactual_fraction = float(os.environ.get("KRAMABENCH_COUNTERFACTUAL_FRACTION", "0"))
    counterfactual_seed = int(os.environ.get("KRAMABENCH_COUNTERFACTUAL_SEED", "42"))
    counterfactual_streak_threshold = int(os.environ.get("KRAMABENCH_COUNTERFACTUAL_STREAK_THRESHOLD", "2"))
    max_counterfactual_simulations = int(os.environ.get("KRAMABENCH_MAX_COUNTERFACTUAL_SIMULATIONS", "1"))
    class ConfiguredKramaBenchSystem(KramaBenchSystem):
        def __init__(self, verbose: bool = False, output_dir=None):
            client = OllamaClient(model=model, base_url=base_url, model_seed=model_seed)
            effective_options = apply_effective_model_options(
                client, num_ctx=num_ctx, num_predict=num_predict,
                required_num_ctx=required_num_ctx,
            )
            (run_root / "model_runtime_preflight.json").write_text(json.dumps({
                "status": "passed", "model": model,
                "scope_required_num_ctx": required_num_ctx,
                "effective_options": effective_options,
                "options_sent_in_ollama_generate_payload": True,
            }, indent=2) + "\n", encoding="utf-8")
            super().__init__(
                verbose=verbose,
                output_dir=output_dir or run_root / "system_scratch" / "KramaBenchPXPSystem",
                client=client,
                max_turns=max_turns,
                max_retries=max_retries,
                task_wall_clock_cap_seconds=task_wall_clock_cap_seconds,
                counterfactual_fraction=counterfactual_fraction,
                counterfactual_seed=counterfactual_seed,
                counterfactual_streak_threshold=counterfactual_streak_threshold,
                max_counterfactual_simulations=max_counterfactual_simulations,
                pipeline_evaluation_enabled=pipeline_evaluation,
            )

    ConfiguredKramaBenchSystem.__name__ = "KramaBenchPXPSystem"
    ConfiguredKramaBenchSystem.__qualname__ = "KramaBenchPXPSystem"
    systems_module = types.ModuleType("systems")
    systems_module.KramaBenchPXPSystem = ConfiguredKramaBenchSystem
    sys.modules["systems"] = systems_module

    sys.argv = [str(evaluate_path), *arguments]
    sys.path.insert(0, str(evaluate_path.parent))
    runpy.run_path(str(evaluate_path), run_name="__main__")


if __name__ == "__main__":
    main()

