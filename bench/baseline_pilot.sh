#!/usr/bin/env bash
# Run the tuning tasks by default, or explicit workload:task-id pairs.
set -u
cd "$(dirname "$0")/.." || exit 1
eval_python="${KRAMABENCH_EVALUATOR_PYTHON:-$HOME/kramabench-eval-venv/bin/python}"
task_python="${PILOT_PYTHON:-$eval_python}"
krama_root="${KRAMABENCH_ROOT:-$HOME/kramabench-pinned}"
if [[ "${1:-}" == "--list-tasks" ]]; then
    if [[ "$#" -ne 1 ]]; then
        printf '%s\n' '--list-tasks takes no additional arguments.' >&2
        exit 1
    fi
    "$task_python" -c 'from bench.kramabench_runner import _load_compatibility_allowlist
for entry in _load_compatibility_allowlist():
    print("{}:{}".format(entry["workload"], entry["task_id"]))'
    exit $?
fi
turns="${TURNS:-6}"
model="${PILOT_MODEL:-qwen3:4b-instruct-2507-q4_K_M}"
tool_calls="${PILOT_TOOL_CALLS:-12}"
request_timeout="${PILOT_REQUEST_TIMEOUT_SECONDS:-300}"
results="${PILOT_RESULTS:-$HOME/blackboard-baseline-$(date -u +%Y%m%dT%H%M%SZ)}"
if ! [[ "$turns" =~ ^([1-9]|1[0-2])$ ]]; then
    printf 'TURNS must be between 1 and 12.\n' >&2; exit 1
fi
if ! [[ "$tool_calls" =~ ^([2-9]|1[0-9]|2[0-4])$ ]]; then
    printf 'PILOT_TOOL_CALLS must be between 2 and 24.\n' >&2; exit 1
fi
if ! [[ "$request_timeout" =~ ^[1-9][0-9]*$ ]]; then
    printf 'PILOT_REQUEST_TIMEOUT_SECONDS must be a positive integer.\n' >&2; exit 1
fi
if [ "$#" -eq 0 ]; then
    set -- astronomy:astronomy-easy-1 environment:environment-easy-1 environment:environment-easy-3 legal:legal-easy-12 legal:legal-easy-4
fi
for path in "$task_python" "$eval_python" "$krama_root/workload"; do
    if [ ! -e "$path" ]; then printf 'Missing: %s\n' "$path" >&2; exit 1; fi
done
if ! "$task_python" -c 'import pydantic' 2>/dev/null; then
    printf 'Install pydantic in %s\n' "$task_python" >&2; exit 1
fi
bridge_pid=""
cleanup() {
    if [ -n "$bridge_pid" ]; then kill "$bridge_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT
if ! curl -fsS --max-time 5 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    if ! /mnt/c/Windows/System32/curl.exe -fsS --max-time 5 http://127.0.0.1:11434/api/version >/dev/null; then
        printf 'Start Ollama for Windows before running the pilot.\n' >&2; exit 1
    fi
    "$task_python" bench/wsl_ollama_bridge.py &
    bridge_pid=$!
    for attempt in 1 2 3 4 5; do
        if curl -fsS --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then break; fi
        sleep 1
    done
    if ! curl -fsS --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
        printf 'Unable to start the WSL Ollama bridge.\n' >&2; exit 1
    fi
fi
mkdir -p "$results"
printf 'Results: %s\n' "$results"
failures=0
for item in "$@"; do
    workload="${item%%:*}"
    task_id="${item#*:}"
    if ! [[ "$item" =~ ^[A-Za-z0-9_-]+:[A-Za-z0-9_.-]+$ ]]; then
        printf 'Invalid workload:task-id pair: %s\n' "$item" >&2
        failures=$((failures + 1))
        continue
    fi
    printf '\n=== %s ===\n' "$task_id"
    "$task_python" -c 'import json,sys; rows=json.load(open(sys.argv[1], encoding="utf-8")); print(next(x["query"] for x in rows if x["id"] == sys.argv[2]))' "$krama_root/workload/$workload.json" "$task_id"
    "$task_python" -m bench.kramabench_runner \
        --kramabench-root "$krama_root" --workload "$workload" --task-id "$task_id" \
        --output-directory "$results/$task_id" --evaluator-python "$eval_python" \
        --evaluator-mode compatibility-no-key --model "$model" --max-turns "$turns" \
        --data-tool-invocation-cap "$tool_calls" --ollama-request-timeout-seconds "$request_timeout" \
        --counterfactual-fraction 0 --approve-genuine-run
    status=$?
    if [ "$status" -ne 0 ]; then failures=$((failures + 1)); fi
    printf '%s: %s\n' "$task_id" "$(if [ "$status" -eq 0 ]; then printf 'completed'; else printf 'failed - inspect run_manifest.json'; fi)"
done
printf '\nFinished with %s failed run(s). Records: %s\n' "$failures" "$results"
if [ "$failures" -ne 0 ]; then exit 1; fi
