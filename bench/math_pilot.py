"""Run a paired, text-only MATH-500 ablation with the existing conversation runner."""

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

from agents.conversation import AGENT_ROSTERS, run_conversation
from agents.llm_client import BASE_URL, MODEL, OllamaClient, inference_usage, parse_response
from agents.pex import PEXResponse

DATASET_REVISION = "6e4ed1a2a79af7d8630a6b768ec859cb5af4d3be"
DATASET_URL = f"https://huggingface.co/datasets/HuggingFaceH4/MATH-500/resolve/{DATASET_REVISION}/test.jsonl"
DATASET_SHA256 = "35dc41080a3680858b27fa7e0533d2d547825316fc5dafe5d316f4ccc5a06132"
TASK_IDS = (
    "test/algebra/694.json", "test/algebra/1199.json", "test/algebra/1282.json",
    "test/intermediate_algebra/1247.json", "test/intermediate_algebra/776.json",
    "test/intermediate_algebra/1849.json", "test/number_theory/45.json",
    "test/number_theory/149.json", "test/number_theory/838.json",
    "test/counting_and_probability/913.json", "test/counting_and_probability/119.json",
    "test/counting_and_probability/216.json",
)
AGENT_COUNT = 6
TURNS = 12
RETRIES = 2
CONDITIONS = {"solo": None, "cf-00": 0.0, "cf-33": 0.33, "cf-50": 0.50, "cf-66": 0.66}
ANSWER_INSTRUCTION = (
    "\n\nPut only the final integer in the prediction field, with no units or Markdown. "
    "Use the explanation field for the main calculation or reasoning step."
)


def integer_answer(text):
    text = text.strip().replace("\N{MINUS SIGN}", "-")
    if text.startswith("$") and text.endswith("$"):
        text = text[1:-1].strip()
    boxed = re.fullmatch(r"\\boxed\{\s*(.*?)\s*\}", text)
    if boxed:
        text = boxed[1]
    if re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", text):
        return int(text.replace(",", ""))
    return None


def load_cases(path):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DATASET_SHA256:
        raise ValueError("MATH-500 dataset hash differs from the pinned revision.")
    rows = [json.loads(line) for line in raw.splitlines()]
    by_id = {row["unique_id"]: row for row in rows}
    if len(rows) != 500 or len(by_id) != len(rows):
        raise ValueError("Expected 500 uniquely identified MATH-500 problems.")
    cases = [by_id[task_id] for task_id in TASK_IDS]
    for index, case in enumerate(cases):
        if (case["level"] != 3 + index % 3 or not case["problem"].strip()
                or "[asy]" in case["problem"].lower() or integer_answer(case["answer"]) is None):
            raise ValueError(f"Invalid text-only integer pilot task: {case['unique_id']}")
    return cases


def run_solo(client, task, directory):
    directory.mkdir(parents=True, exist_ok=True)
    response = None
    prediction = None
    failure = None
    try:
        response = client.generate(
            task, system="Solve the problem independently and return the requested JSON.",
            schema=PEXResponse.model_json_schema(),
        )
        prediction = parse_response(response, PEXResponse).prediction
        outcome = "answered"
    except ValueError as exc:
        outcome, failure = "invalid_response", str(exc)
    except RuntimeError as exc:
        outcome, failure = "error", str(exc)
    except KeyboardInterrupt:
        outcome = "interrupted"
    trace = directory / "response.json"
    trace.write_text(json.dumps({"task": task, "raw": response, "failure": failure}, indent=2),
                     encoding="utf-8")
    return {
        "outcome": outcome, "prediction": prediction, "trace": str(trace),
        "usage": inference_usage([{"raw": response}]),
    }


def run_board(client, task, directory, task_id, fraction, seed):
    report, trace = run_conversation(
        client, task, TURNS, RETRIES, directory, task_id=task_id, agent_count=AGENT_COUNT,
        counterfactual_fraction=fraction, counterfactual_seed=seed, retrospective_recovery=True,
    )
    entries = report["board"]["entries"]
    consensus = report["outcome"] == "scheduler_consensus"
    replays = report["counterfactual_attempts"]
    usage = [turn["usage"] for turn in report["turns"] if "usage" in turn]
    usage.extend(attempt.get("usage", {}) for attempt in replays)
    return {
        "outcome": report["outcome"], "prediction": entries[-1]["prediction"] if consensus else None,
        "trace": str(trace), "turns": len(report["turns"]),
        "eligible_agents": report["counterfactual_capable_agents"], "eligibility_seed": seed,
        "deadlocks": len(report["board"]["deadlocks"]),
        "replays": report["workflow_counters"]["counterfactual_simulations"],
        "accepted_replays": sum(attempt["accepted"] for attempt in replays),
        "applied_replays": sum(attempt["applied_to_live"] for attempt in replays),
        "usage": {key: sum(item.get(key, 0) for item in usage)
                  for key in ("calls", "prompt_tokens", "output_tokens")},
    }


def run_pilot(client, cases, output):
    records = []
    total_runs = len(cases) * len(CONDITIONS)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "summary.jsonl").open("x", encoding="utf-8") as log:
        for index, case in enumerate(cases):
            task = case["problem"] + ANSWER_INSTRUCTION
            task_id = "math500-" + case["unique_id"].removeprefix("test/").removesuffix(".json").replace("/", "-")
            expected = integer_answer(case["answer"])
            for condition, fraction in CONDITIONS.items():
                print(f"\n[{len(records) + 1}/{total_runs}] {case['unique_id']} "
                      f"(level {case['level']}) | {condition}", flush=True)
                started = time.monotonic()
                if fraction is None:
                    run = run_solo(client, task, output / condition / task_id)
                else:
                    run = run_board(client, task, output / condition, task_id, fraction, 42 + index)
                parsed = integer_answer(run["prediction"]) if run["prediction"] is not None else None
                record = {
                    "task_id": case["unique_id"], "level": case["level"], "condition": condition,
                    "requested_fraction": fraction, "expected": expected, "parsed_answer": parsed,
                    "correct": parsed == expected, "wall_seconds": round(time.monotonic() - started, 3),
                    **run,
                }
                records.append(record)
                log.write(json.dumps(record) + "\n")
                log.flush()
                print(f"{condition}: {record['outcome']} | correct={record['correct']}", flush=True)
                if record["outcome"] == "interrupted":
                    return records
    return records


def summarize(records):
    baseline = {row["task_id"]: row for row in records if row["condition"] == "cf-00"}
    summary = {}
    for condition in CONDITIONS:
        runs = [row for row in records if row["condition"] == condition]
        paired = [(baseline[row["task_id"]], row) for row in runs if row["task_id"] in baseline]
        summary[condition] = {
            "runs": len(runs),
            "correct": sum(row["correct"] for row in runs),
            "accuracy": sum(row["correct"] for row in runs) / len(runs) if runs else None,
            "wrong_consensus": sum(row["outcome"] == "scheduler_consensus"
                                   and row["parsed_answer"] is not None and not row["correct"] for row in runs),
            "invalid_final_answers": sum(row["outcome"] in {"answered", "scheduler_consensus"}
                                         and row["parsed_answer"] is None for row in runs),
            "outcomes": {outcome: sum(row["outcome"] == outcome for row in runs)
                         for outcome in sorted({row["outcome"] for row in runs})},
            "tasks_with_deadlock": sum(row.get("deadlocks", 0) > 0 for row in runs),
            "replays": sum(row.get("replays", 0) for row in runs),
            "applied_replays": sum(row.get("applied_replays", 0) for row in runs),
            "model_calls": sum(row["usage"]["calls"] for row in runs),
            "prompt_tokens": sum(row["usage"]["prompt_tokens"] for row in runs),
            "output_tokens": sum(row["usage"]["output_tokens"] for row in runs),
            "paired_gains_vs_cf_00": sum(not base["correct"] and row["correct"] for base, row in paired),
            "paired_losses_vs_cf_00": sum(base["correct"] and not row["correct"] for base, row in paired),
        }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--tasks", type=int, choices=range(1, len(TASK_IDS) + 1), default=len(TASK_IDS))
    parser.add_argument("--check", action="store_true", help="Verify the pinned dataset and list cases without inference")
    args = parser.parse_args()
    if not args.check and args.output is None:
        parser.error("--output is required when running the pilot")
    try:
        cases = load_cases(args.dataset)[:args.tasks]
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    if args.check:
        for case in cases:
            print(f"Level {case['level']} | {case['subject']} | {case['unique_id']}")
        return 0

    client = OllamaClient(args.model, args.base_url)
    records = run_pilot(client, cases, args.output)
    report = {
        "dataset": DATASET_URL, "dataset_sha256": DATASET_SHA256, "model": client.model,
        "model_options": client.options, "task_ids": [case["unique_id"] for case in cases],
        "conditions": CONDITIONS, "roster": AGENT_ROSTERS[AGENT_COUNT], "max_turns": TURNS,
        "max_retries": RETRIES, "complete": len(records) == len(cases) * len(CONDITIONS)
        and all(row["outcome"] != "interrupted" for row in records),
        "results": summarize(records),
    }
    summary = args.output / "summary.json"
    summary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for label, result in report["results"].items():
        print(f"{label}: {result['correct']}/{result['runs']} correct, "
              f"{result['tasks_with_deadlock']} deadlocks, {result['replays']} replays", flush=True)
    print(f"Summary: {summary}", flush=True)
    return int(not report["complete"] or any(row["outcome"] == "error" for row in records))


if __name__ == "__main__":
    raise SystemExit(main())
