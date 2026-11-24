"""Compare blackboard recovery off and on using pinned CounterBench tasks."""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from agents.conversation import AGENT_ROSTERS
from agents.llm_client import BASE_URL, MODEL, ROOT, OllamaClient
from bench.math_pilot import run_board, summarize


REVISION = "c6225dfa00b7a89ccfeae67797b9a6d80e48e44c"
DATASET_URL = f"https://huggingface.co/datasets/CounterBench/CounterBench/resolve/{REVISION}"
DATASET_DIR = ROOT / "bench/data/counterbench_raw"
ALPHA = "data_balanced_alpha_V1.json"
BACKDOOR = "data_balanced_backdoor_V2.json"
FILE_HASHES = {
    ALPHA: "39f935a48fb997869c1e4488c9a2b902dc08acd876da557c675722c269391a76",
    BACKDOOR: "e04eb6e58508ef3d6c40051a8f8976dff9486dea3190e5a47418d3bca5ef1771",
}
TASKS = (
    (ALPHA, "800", "basic"),
    (ALPHA, "940", "joint"),
    (ALPHA, "392", "nested"),
    (ALPHA, "409", "conditional"),
    (BACKDOOR, "200", "backdoor"),
)
# The extended set interleaves the five types and covers graph5 through graph9.
# Each type has three yes and three no answers and excludes the pilot examples.
EXTENDED_TASKS = (
    (ALPHA, "807", "basic"),
    (ALPHA, "133", "joint"),
    (ALPHA, "382", "nested"),
    (ALPHA, "460", "conditional"),
    (BACKDOOR, "6", "backdoor"),
    (ALPHA, "210", "basic"),
    (ALPHA, "339", "joint"),
    (ALPHA, "170", "nested"),
    (ALPHA, "616", "conditional"),
    (BACKDOOR, "15", "backdoor"),
    (ALPHA, "91", "basic"),
    (ALPHA, "147", "joint"),
    (ALPHA, "587", "nested"),
    (ALPHA, "67", "conditional"),
    (BACKDOOR, "218", "backdoor"),
    (ALPHA, "434", "basic"),
    (ALPHA, "901", "joint"),
    (ALPHA, "954", "nested"),
    (ALPHA, "72", "conditional"),
    (BACKDOOR, "228", "backdoor"),
    (ALPHA, "498", "basic"),
    (ALPHA, "711", "joint"),
    (ALPHA, "150", "nested"),
    (ALPHA, "275", "conditional"),
    (BACKDOOR, "235", "backdoor"),
    (ALPHA, "228", "basic"),
    (ALPHA, "544", "joint"),
    (ALPHA, "189", "nested"),
    (ALPHA, "263", "conditional"),
    (BACKDOOR, "21", "backdoor"),
)
CASE_SETS = {"pilot": TASKS, "extended": EXTENDED_TASKS}
AGENT_COUNT = 3
CONDITIONS = {"cf-00": 0.0, "cf-100": 1.0}


def load_cases(*, download=False, tasks=TASKS):
    sources = {}
    for filename, expected_hash in FILE_HASHES.items():
        path = DATASET_DIR / filename
        fetch = download and not path.exists()
        if fetch:
            with urlopen(f"{DATASET_URL}/{filename}", timeout=30) as response:
                raw = response.read()
        else:
            raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise ValueError(f"CounterBench dataset hash mismatch for {path}")
        if fetch:
            DATASET_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        sources[filename] = {str(row["question_id"]): row for row in json.loads(raw)}

    cases = []
    for filename, question_id, kind in tasks:
        row = sources[filename][question_id]
        if (row["type"] != kind or row["answer"] not in {"yes", "no"}
                or not row["given_info"].strip() or not row["question"].strip()):
            raise ValueError(f"Invalid CounterBench task {filename}:{question_id}")
        cases.append(row)
    return cases


def run_pilot(client, cases, output, turns, retries):
    records = []
    total_runs = len(cases) * len(CONDITIONS)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "summary.jsonl").open("x", encoding="utf-8") as log:
        for index, case in enumerate(cases):
            task_id = f"counterbench-{case['type']}-{case['question_id']}"
            task = (
                f"{case['given_info'].strip()}\n\n{case['question'].strip()}\n\n"
                "Use only yes or no in the prediction field. Explain the decisive causal step."
            )
            for condition, fraction in CONDITIONS.items():
                print(f"\n[{len(records) + 1}/{total_runs}] {task_id} | {condition}", flush=True)
                started = time.monotonic()
                run = run_board(client, task, output / condition, task_id, fraction, 42 + index,
                                turns=turns, retries=retries, agent_count=AGENT_COUNT)
                prediction = (run["prediction"] or "").strip().casefold()
                parsed_answer = prediction if prediction in {"yes", "no"} else None
                row = {
                    "task_id": task_id,
                    "question_type": case["type"],
                    "question_id": case["question_id"],
                    "graph_id": case["meta"]["graph_id"],
                    "condition": condition,
                    "requested_fraction": fraction,
                    "expected": case["answer"],
                    "parsed_answer": parsed_answer,
                    "correct": parsed_answer == case["answer"],
                    "wall_seconds": round(time.monotonic() - started, 3),
                    **run,
                }
                records.append(row)
                log.write(json.dumps(row) + "\n")
                log.flush()
                print(f"{run['outcome']} | correct={row['correct']} | replays={run['replays']}", flush=True)
                if run["outcome"] == "interrupted":
                    return records
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", action="store_true", help="Download and verify data without inference")
    parser.add_argument("--case-set", choices=CASE_SETS, default="pilot",
                        help="Use the five-question pilot or the 30-question extended set")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--no-explanation-review", dest="review_explanations", action="store_false",
                        help="Skip the model explanation check in live and simulated turns")
    parser.add_argument("--output", type=Path, help="Directory for this pilot's transcripts and scores")
    parser.add_argument("--turns", type=int, choices=range(4, 13), default=10)
    parser.add_argument("--retries", type=int, choices=range(6), default=2)
    args = parser.parse_args()
    tasks = CASE_SETS[args.case_set]
    try:
        cases = load_cases(download=args.setup, tasks=tasks)
    except FileNotFoundError as exc:
        parser.error(f"{exc.filename} is missing. Run make counterbench-setup first.")
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    if args.setup:
        for case in cases:
            print(f"{case['type']} | {case['question_id']}")
        print(f"Verified {len(cases)} CounterBench {args.case_set} questions at revision {REVISION}")
        return 0

    client = OllamaClient(args.model, BASE_URL, review_explanations=args.review_explanations)
    print(f"Pre-board explanation review: {'on' if client.review_explanations else 'off'}", flush=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = args.output or ROOT / "results/counterbench" / f"{args.case_set}-{stamp}"
    records = run_pilot(client, cases, output, args.turns, args.retries)
    complete = (len(records) == len(cases) * len(CONDITIONS)
                and all(row["outcome"] != "interrupted" for row in records))
    report = {
        "dataset_revision": REVISION,
        "dataset_sources": {f"{DATASET_URL}/{name}": sha for name, sha in FILE_HASHES.items()},
        "case_set": args.case_set,
        "cases": [{"file": name, "question_id": identifier, "type": kind}
                  for name, identifier, kind in tasks],
        "conditions": CONDITIONS,
        "model": client.model,
        "model_options": client.options,
        "self_check_enabled": client.review_explanations,
        "roster": AGENT_ROSTERS[AGENT_COUNT],
        "max_turns": args.turns,
        "max_retries": args.retries,
        "complete": complete,
        "results": summarize(records, CONDITIONS),
    }
    summary = output / "summary.json"
    summary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for label, result in report["results"].items():
        print(f"{label}: {result['correct']}/{result['runs']} correct, "
              f"{result['tasks_with_deadlock']} deadlocks, {result['replays']} replays")
    print(f"Summary: {summary}")
    return int(not complete or any(row["outcome"] == "error" for row in records))


if __name__ == "__main__":
    raise SystemExit(main())
