"""Summarize saved KramaBench attempts, including failures and recovery activity."""
import argparse
import csv
import json
from pathlib import Path


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def summarize(root):
    rows = []
    for path in sorted(root.rglob("run_manifest.json")):
        manifest = load(path)
        if not isinstance(manifest, dict) or not manifest.get("task_id"):
            continue
        task = manifest["task_id"]
        records = list(path.parent.glob("system_scratch/**/krama_task_records/*.json"))
        record = next((data for p in records if isinstance((data := load(p)), dict)
                       and data.get("task_id") == task), {})
        transcripts = list(path.parent.glob("system_scratch/**/krama_conversations/**/transcript.json"))
        transcript = next((data for p in transcripts if isinstance((data := load(p)), dict)
                           and data.get("task_id") == task), {})
        counters = manifest.get("workflow_counters") or record.get("workflow_counters") or {}
        failures = transcript.get("failures") or []
        reason = (manifest.get("evaluator_process") or {}).get("task_failure_summary") or manifest.get("error") or ""
        if not reason and failures:
            reason = "; ".join(str(f.get("error") or f.get("category") or "") for f in failures if isinstance(f, dict))
        scores = {}
        if manifest.get("status") == "success":
            measure_path = manifest.get("compatibility_evaluator_task_measures") or manifest.get("official_evaluator_task_measures")
            if measure_path and Path(measure_path).is_file():
                with Path(measure_path).open(encoding="utf-8", newline="") as file:
                    for item in csv.DictReader(file):
                        if item.get("task_id") == task:
                            if item.get("metric") in ("success", "rae_score", "f1", "precision", "recall"):
                                scores[item["metric"]] = item.get("value")
        rows.append({"task": task, "status": manifest.get("status"),
                     "model": manifest.get("model"),
                     "fraction": manifest.get("counterfactual_fraction"),
                     "retrospective_recovery": manifest.get("retrospective_recovery", False),
                     "outcome": transcript.get("outcome"), "turns": len(transcript.get("turns", [])),
                     "triggered": counters.get("counterfactual_triggers", 0),
                     "simulations": counters.get("counterfactual_simulations", 0),
                     "changed": counters.get("counterfactual_changed_outcome", 0),
                     "data_loop_degraded": manifest.get("data_loop_degraded", False),
                     "data_loop_degradation_reason": manifest.get("data_loop_degradation_reason"),
                     "data_tool_calls": (record.get("data_access_diagnostics") or {}).get("tool_invocations"),
                     "turn_failures": failures,
                     "score": scores or None, "failure": reason or None,
                     "context_tokens": (manifest.get("model_options") or {}).get("num_ctx"),
                     "manifest": str(path)})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="Directory containing run_manifest.json files")
    args = parser.parse_args()
    if not args.results.is_dir():
        parser.error("results directory does not exist")
    rows = summarize(args.results)
    if not rows:
        print("No task manifests found.")
        return 1
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
