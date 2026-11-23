"""Run a paired, text-only MATH-500 ablation with the existing conversation runner."""

import argparse
from collections import Counter
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
    "test/algebra/1332.json", "test/algebra/824.json", "test/algebra/2277.json",
    "test/intermediate_algebra/1151.json", "test/intermediate_algebra/121.json",
    "test/intermediate_algebra/117.json", "test/number_theory/183.json",
    "test/number_theory/1065.json", "test/number_theory/769.json",
    "test/counting_and_probability/23957.json", "test/counting_and_probability/10.json",
    "test/counting_and_probability/159.json",
)
DEFAULT_TASK_COUNT = 12
AGENT_COUNT = 6
TURNS = 12
RETRIES = 2
CONDITIONS = {"solo": None, "cf-00": 0.0, "cf-33": 0.33, "cf-50": 0.50, "cf-66": 0.66}
CONSENSUS_OUTCOMES = {"scheduler_consensus", "consensus_needs_review"}
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


def final_board_prediction(report):
    # An earlier tag warning does not undo the board's final unanimous agreement.
    if (report["outcome"] in CONSENSUS_OUTCOMES
            and report["scheduler_status"] in {"STRONG", "ULTRA_STRONG"}):
        return report["board"]["entries"][-1]["prediction"]
    return None


def score_answer(prediction, expected):
    parsed = integer_answer(prediction) if prediction is not None else None
    return {"parsed_answer": parsed, "correct": parsed is not None and parsed == expected}


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


def load_deadlocked_ids(paths):
    task_ids = set()
    for path in paths:
        with path.open(encoding="utf-8-sig") as log:
            for number, line in enumerate(log, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{number}: invalid JSONL record") from exc
                if row["outcome"] == "deadlocked" or row.get("deadlocks", 0) > 0:
                    task_ids.add(row["task_id"])
    unknown = task_ids.difference(TASK_IDS)
    if unknown:
        raise ValueError(f"Deadlocked task IDs outside the pinned pilot: {', '.join(sorted(unknown))}")
    if not task_ids:
        raise ValueError("No deadlocked math problems found in the supplied summaries")
    return task_ids


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
        counterfactual_include_first_agent=True,
    )
    replays = report["counterfactual_attempts"]
    usage = [turn["usage"] for turn in report["turns"] if "usage" in turn]
    usage.extend(attempt.get("usage") or {} for attempt in replays)
    return {
        "outcome": report["outcome"], "prediction": final_board_prediction(report),
        "trace": str(trace), "turns": len(report["turns"]),
        "eligible_agents": report["counterfactual_capable_agents"], "eligibility_seed": seed,
        "deadlocks": len(report["board"]["deadlocks"]),
        "replays": report["workflow_counters"]["counterfactual_simulations"],
        "accepted_replays": sum(attempt["accepted"] for attempt in replays),
        "applied_replays": sum(attempt["applied_to_live"] for attempt in replays),
        "replay_sources": [attempt["proposal_source"] for attempt in replays],
        "counterfactual_skips": report["counterfactual_skips"],
        "usage": {key: sum(item.get(key, 0) for item in usage)
                  for key in ("calls", "prompt_tokens", "output_tokens")},
    }


def run_pilot(client, cases, output):
    records = []
    total_runs = len(cases) * len(CONDITIONS)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "summary.jsonl").open("x", encoding="utf-8") as log:
        for case in cases:
            task = case["problem"] + ANSWER_INSTRUCTION
            task_id = "math500-" + case["unique_id"].removeprefix("test/").removesuffix(".json").replace("/", "-")
            expected = integer_answer(case["answer"])
            seed = 42 + TASK_IDS.index(case["unique_id"])
            for condition, fraction in CONDITIONS.items():
                print(f"\n[{len(records) + 1}/{total_runs}] {case['unique_id']} "
                      f"(level {case['level']}) | {condition}", flush=True)
                started = time.monotonic()
                if fraction is None:
                    run = run_solo(client, task, output / condition / task_id)
                else:
                    run = run_board(client, task, output / condition, task_id, fraction, seed)
                record = {
                    "task_id": case["unique_id"], "level": case["level"], "condition": condition,
                    "requested_fraction": fraction, "expected": expected,
                    **score_answer(run["prediction"], expected),
                    "wall_seconds": round(time.monotonic() - started, 3),
                    **run,
                }
                records.append(record)
                log.write(json.dumps(record) + "\n")
                log.flush()
                print(f"{condition}: {record['outcome']} | correct={record['correct']}", flush=True)
                if record["outcome"] == "interrupted":
                    return records
    return records


def rescore_saved_run(directory):
    records = []
    with (directory / "summary.jsonl").open(encoding="utf-8") as log:
        for line in log:
            row = json.loads(line)
            if row["outcome"] == "consensus_needs_review":
                trace = Path(row["trace"])
                row["prediction"] = final_board_prediction(json.loads(trace.read_text(encoding="utf-8")))
            row.update(score_answer(row["prediction"], row["expected"]))
            records.append(row)
    return summarize(records)


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
            "correct_needs_review": sum(row["outcome"] == "consensus_needs_review"
                                        and row["correct"] for row in runs),
            "wrong_consensus": sum(row["outcome"] in CONSENSUS_OUTCOMES
                                   and row["parsed_answer"] is not None and not row["correct"] for row in runs),
            "invalid_final_answers": sum((row["outcome"] == "answered" or row["outcome"] in CONSENSUS_OUTCOMES)
                                         and row["parsed_answer"] is None for row in runs),
            "outcomes": {outcome: sum(row["outcome"] == outcome for row in runs)
                         for outcome in sorted({row["outcome"] for row in runs})},
            "tasks_with_deadlock": sum(row.get("deadlocks", 0) > 0 for row in runs),
            "replays": sum(row.get("replays", 0) for row in runs),
            "accepted_replays": sum(row.get("accepted_replays", 0) for row in runs),
            "applied_replays": sum(row.get("applied_replays", 0) for row in runs),
            "replay_sources": dict(Counter(source for row in runs for source in row.get("replay_sources", []))),
            "replay_skip_reasons": dict(Counter(
                skip["reason"] for row in runs for skip in row.get("counterfactual_skips", [])
            )),
            "model_calls": sum(row["usage"]["calls"] for row in runs),
            "prompt_tokens": sum(row["usage"]["prompt_tokens"] for row in runs),
            "output_tokens": sum(row["usage"]["output_tokens"] for row in runs),
            "paired_gains_vs_cf_00": sum(not base["correct"] and row["correct"] for base, row in paired),
            "paired_losses_vs_cf_00": sum(base["correct"] and not row["correct"] for base, row in paired),
        }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset", type=Path)
    source.add_argument("--rescore", type=Path, help="Regrade a saved pilot directory without inference")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--start", type=int, choices=range(1, len(TASK_IDS) + 1),
                        help="First problem in the pinned list, numbered from 1")
    parser.add_argument("--tasks", type=int, choices=range(1, len(TASK_IDS) + 1),
                        help="Number of consecutive problems starting at --start")
    parser.add_argument("--deadlocks-from", type=Path, nargs="+",
                        help="Run only pinned problems that deadlocked in the supplied summary.jsonl files")
    parser.add_argument("--check", action="store_true", help="Verify the pinned dataset and list cases without inference")
    args = parser.parse_args()
    if args.rescore:
        try:
            print(json.dumps(rescore_saved_run(args.rescore), indent=2))
        except (OSError, ValueError, KeyError) as exc:
            parser.error(str(exc))
        return 0
    if not args.check and args.output is None:
        parser.error("--output is required when running the pilot")
    if args.deadlocks_from and (args.start is not None or args.tasks is not None):
        parser.error("--deadlocks-from selects the problems instead of --start and --tasks")
    start_index = (args.start or 1) - 1
    end_index = start_index + (args.tasks or DEFAULT_TASK_COUNT)
    if end_index > len(TASK_IDS):
        parser.error(f"--start and --tasks must select problems within the {len(TASK_IDS)}-problem list")
    try:
        cases = load_cases(args.dataset)
        if args.deadlocks_from:
            deadlocked_ids = load_deadlocked_ids(args.deadlocks_from)
            cases = [case for case in cases if case["unique_id"] in deadlocked_ids]
        else:
            cases = cases[start_index:end_index]
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
        "model_options": client.options, "first_task": TASK_IDS.index(cases[0]["unique_id"]) + 1,
        "task_ids": [case["unique_id"] for case in cases],
        "deadlocks_from": [str(path) for path in args.deadlocks_from] if args.deadlocks_from else [],
        "conditions": CONDITIONS, "roster": AGENT_ROSTERS[AGENT_COUNT], "max_turns": TURNS,
        "counterfactual_include_first_agent": True,
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
