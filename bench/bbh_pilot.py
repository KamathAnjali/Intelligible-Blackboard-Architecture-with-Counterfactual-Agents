"""Local, pinned BBH formal-fallacy and human causal-judgment pilots.

Only --setup downloads data. Reference targets are read by the evaluator and
never included in the conversation task or counterfactual guidance.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import time
from urllib.request import urlopen

from agents.conversation import AGENT_ROSTERS
from agents.llm_client import BASE_URL, MODEL, ROOT, OllamaClient
from agents.session_suite import prompt_fingerprints
from bench.math_pilot import run_board, summarize
from blackboard.core import DEADLOCK_WINDOW


REVISION = "9ee07bd481feebf959a6b59d61ea57bdcf30964d"
SOURCE = f"https://raw.githubusercontent.com/suzgunmirac/BIG-Bench-Hard/{REVISION}"
DATASET_DIR = ROOT / "bench/data/bbh_raw"
SUITES = {
    "fallacies": {"file": "formal_fallacies.json", "count": 250,
                  "sha256": "4c1adb664821157faade1d4153685403afcc02648f3c180a608a18f17f589629",
                  "answers": ("valid", "invalid")},
    "causal": {"file": "causal_judgement.json", "count": 187,
               "sha256": "6e8bca44dd402008ed41c07a3d0a1d5e7b03db7001a7e002fcdf4c60985ea081",
               "answers": ("yes", "no")},
}
LICENSE_SHA256 = "4ef2ff4295d26ab6211235039c132697408cec5391d2c091b41e983771978db8"
CONDITIONS = {"off": {"cf-00": 0.0}, "on": {"cf-100": 1.0},
              "both": {"cf-00": 0.0, "cf-100": 1.0}}


def verified_bytes(path, remote_path, expected_hash, download):
    if path.exists():
        raw = path.read_bytes()
    elif download:
        with urlopen(f"{SOURCE}/{remote_path}", timeout=30) as response:
            raw = response.read()
    else:
        raise FileNotFoundError(f"Missing {path}. Run make bbh-setup first.")
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError(f"BBH checksum mismatch: {path}. Restore the pinned source file.")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return raw


def load_cases(suites, tasks, start, *, download=False):
    verified_bytes(DATASET_DIR / "LICENSE", "LICENSE", LICENSE_SHA256, download)
    cases = []
    for suite in suites:
        spec = SUITES[suite]
        raw = verified_bytes(DATASET_DIR / spec["file"], f"bbh/{spec['file']}",
                             spec["sha256"], download)
        examples = json.loads(raw)["examples"]
        if len(examples) != spec["count"]:
            raise ValueError(f"Unexpected example count for {suite}")
        for row in examples:
            if (not isinstance(row.get("input"), str) or not row["input"].strip()
                    or row.get("target", "").strip().casefold() not in spec["answers"]):
                raise ValueError(f"Invalid BBH example in {suite}")
        if start + tasks > len(examples):
            raise ValueError(f"{suite} contains {len(examples)} rows; start + tasks exceeds it")
        # Fixed source-index permutation, independent of labels or model outcomes.
        indices = list(range(len(examples)))
        random.Random(42).shuffle(indices)
        for index in indices[start:start + tasks]:
            row = examples[index]
            cases.append({"task_id": f"bbh-{suite}-{index:03d}", "suite": suite,
                          "source_index": index, "input": row["input"],
                          "expected": row["target"].strip().casefold()})
    return cases


def task_text(case):
    answers = "valid or invalid" if case["suite"] == "fallacies" else "yes or no"
    return (case["input"].strip() + f"\n\nUse only {answers} in the prediction field. "
            "Explain the decisive reasoning in the explanation field.")


def trace_diagnostics(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    reviews = {turn["entry"]["entry_id"] for turn in report["turns"]
               if turn.get("posted") and turn.get("review_rejected") and "entry" in turn}
    entries = report["board"]["entries"]
    positions = {entry["entry_id"]: index for index, entry in enumerate(entries)}
    sources = Counter()
    for event in report["board"]["deadlocks"]:
        end = positions[event["triggered_at_entry_id"]] + 1
        window = entries[max(0, end - DEADLOCK_WINDOW):end]
        rejected_count = sum(entry["entry_id"] in reviews for entry in window)
        source = ("agent_entries_only" if rejected_count == 0 else
                  "reviewer_only" if rejected_count == len(window) else "mixed")
        sources[source] += 1
    return {"reviewer_rejections": len(reviews), "deadlock_sources": dict(sources),
            "latest_by_agent": {
                agent_id: next(({"tag": entry["tag"], "prediction": entry["prediction"]}
                                for entry in reversed(entries)
                                if entry["agent_id"] == agent_id and not entry["is_counterfactual_sim"]), None)
                for agent_id, agent in report["board"]["agents"].items() if agent["active"]}}


def write_summary(path, metadata, records, planned):
    results = {}
    for suite in metadata["suites"]:
        selected = [row for row in records if row["suite"] == suite]
        results[suite] = summarize(selected, metadata["conditions"])
        for condition, stats in results[suite].items():
            runs = [row for row in selected if row["condition"] == condition]
            stats["usage_complete"] = all(row.get("usage_complete", True) for row in runs)
            stats["reviewer_rejections"] = sum(row.get("reviewer_rejections", 0) for row in runs)
            stats["deadlock_sources"] = dict(Counter(
                source for row in runs for source, count in row.get("deadlock_sources", {}).items()
                for _ in range(count)))
    complete = len(records) == planned and all(row["outcome"] != "interrupted" for row in records)
    summary = {**metadata, "planned_runs": planned, "recorded_runs": len(records),
               "complete": complete, "results": results}
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def run_pilot(client, cases, output, args):
    output.mkdir(parents=True, exist_ok=True)
    conditions = CONDITIONS[args.condition]
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_revision": REVISION, "sources": {
            f"{SOURCE}/bbh/{SUITES[suite]['file']}": SUITES[suite]["sha256"] for suite in args.suites},
        "suites": args.suites, "conditions": conditions,
        "source_license": {"url": f"{SOURCE}/LICENSE", "sha256": LICENSE_SHA256},
        "selection": "source indices shuffled with seed 42, then sliced by start and tasks",
        "start": args.start, "tasks_per_suite": args.tasks,
        "cases": [{key: value for key, value in case.items() if key not in {"input", "expected"}}
                  for case in cases],
        "model": client.model, "model_options": client.options,
        "self_check_enabled": client.review_explanations, "prompt_sha256": prompt_fingerprints(),
        "roster": AGENT_ROSTERS[3], "max_turns": args.turns, "max_retries": args.retries,
        "protocol": "existing sequential three-agent conversation; no forced answers or tags",
        "scoring": "exact normalized label on final consensus; unresolved runs count as incorrect",
        "causal_label_meaning": "agreement with the dataset's human causal judgment, not objective causal truth",
    }
    metadata["ollama_version"] = client.request("/api/version")
    tags = client.request("/api/tags").get("models", [])
    metadata["model_digest"] = next((item.get("digest") for item in tags
                                      if item.get("name") == client.model), None)
    records = []
    planned = len(cases) * len(conditions)
    summary_path = output / "summary.json"
    write_summary(summary_path, metadata, records, planned)
    with (output / "summary.jsonl").open("x", encoding="utf-8") as log:
        try:
            for case in cases:
                for condition, fraction in conditions.items():
                    print(f"\n[{len(records) + 1}/{planned}] {case['task_id']} | {condition}", flush=True)
                    started = time.monotonic()
                    trace = output / condition / case["task_id"] / "transcript.json"
                    try:
                        run = run_board(client, task_text(case), output / condition, case["task_id"],
                                        fraction, 42 + case["source_index"], turns=args.turns,
                                        retries=args.retries, agent_count=3)
                        run.update(trace_diagnostics(Path(run["trace"])))
                    except Exception as exc:
                        run = {"outcome": "runner_error", "prediction": None,
                               "trace": str(trace) if trace.exists() else None, "error": str(exc),
                               "usage": {"calls": 0, "prompt_tokens": 0, "output_tokens": 0},
                               "usage_complete": False}
                    prediction = run.get("prediction")
                    parsed = prediction.strip().casefold() if isinstance(prediction, str) else None
                    if parsed not in SUITES[case["suite"]]["answers"]:
                        parsed = None
                    record = {**run, "task_id": case["task_id"], "suite": case["suite"],
                              "source_index": case["source_index"], "condition": condition,
                              "expected": case["expected"], "parsed_answer": parsed,
                              "correct": parsed is not None and parsed == case["expected"],
                              "wall_seconds": round(time.monotonic() - started, 3)}
                    records.append(record)
                    log.write(json.dumps(record, ensure_ascii=False) + "\n")
                    log.flush()
                    write_summary(summary_path, metadata, records, planned)
                    print(f"{record['outcome']} | correct={record['correct']} | "
                          f"deadlocks={record.get('deadlocks', 0)} | replays={record.get('replays', 0)}", flush=True)
                    if record["outcome"] == "interrupted":
                        return records
        except KeyboardInterrupt:
            print("Interrupted; completed records and partial transcripts are retained.", flush=True)
        finally:
            write_summary(summary_path, metadata, records, planned)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--setup", action="store_true", help="Download and verify pinned data without inference")
    action.add_argument("--check", action="store_true", help="Verify local data and list selected cases without inference")
    parser.add_argument("--suite", choices=("all", *SUITES), default="all")
    parser.add_argument("--tasks", type=int, choices=range(1, 251), default=5, help="Cases per selected suite")
    parser.add_argument("--start", type=int, default=0, help="Offset into the fixed shuffled source indices")
    parser.add_argument("--condition", choices=CONDITIONS, default="off",
                        help="Recovery condition; defaults to off")
    parser.add_argument("--turns", type=int, choices=range(4, 13), default=12)
    parser.add_argument("--retries", type=int, choices=range(6), default=2)
    parser.add_argument("--explanation-review", action="store_true", help="Enable the model reviewer; default off")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.start < 0:
        parser.error("start must be non-negative")
    args.suites = list(SUITES) if args.suite == "all" else [args.suite]
    try:
        cases = load_cases(args.suites, args.tasks, args.start, download=args.setup)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    if args.setup or args.check:
        for suite in args.suites:
            print(f"{suite}: verified {SUITES[suite]['count']} local questions")
        print(f"Pinned revision: {REVISION}")
        for case in cases:
            print(case["task_id"])
        print(f"Selected {len(cases)} cases. No inference performed.")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = args.output or ROOT / "results/bbh" / f"{args.suite}-{stamp}"
    if (output / "summary.json").exists() or (output / "summary.jsonl").exists():
        parser.error("Output already contains a run. Use a fresh output directory.")
    client = OllamaClient(args.model, args.base_url, review_explanations=args.explanation_review)
    print(f"Pre-board explanation review: {'on' if args.explanation_review else 'off'}", flush=True)
    records = run_pilot(client, cases, output, args)
    print(f"Summary: {output / 'summary.json'}", flush=True)
    complete = len(records) == len(cases) * len(CONDITIONS[args.condition])
    return int(not complete or any(row["outcome"] in {"error", "runner_error", "interrupted"} for row in records))


if __name__ == "__main__":
    raise SystemExit(main())
