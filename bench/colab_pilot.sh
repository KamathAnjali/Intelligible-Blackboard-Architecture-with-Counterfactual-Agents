#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

runtime="${COLAB_PILOT_RUNTIME:-/content/blackboard-runtime}"
export KRAMABENCH_ROOT="${KRAMABENCH_ROOT:-$runtime/kramabench-pinned}"
export KRAMABENCH_EVALUATOR_VENV="${KRAMABENCH_EVALUATOR_VENV:-$runtime/evaluator}"
export KRAMABENCH_EVALUATOR_PYTHON="$KRAMABENCH_EVALUATOR_VENV/bin/python"
export PILOT_PYTHON="$KRAMABENCH_EVALUATOR_PYTHON"
export PYTHONDONTWRITEBYTECODE=1
python="$PILOT_PYTHON"
host_python="${PYTHON:-python3}"
math_venv="$runtime/math-venv"
math_dataset="$runtime/math500-test.jsonl"
mode="${1:-}"
math_selection=(--start "${MATH_START:-1}" --tasks "${MATH_TASKS:-12}")
if [[ "$mode" == math-* || "$mode" == counterbench-* ]]; then
    python="$math_venv/bin/python"
fi

offline_checks() {
    for script in bench/colab_pilot.sh bench/baseline_pilot.sh bench/setup_krama_checkout.sh; do
        bash -n "$script"
    done
    "$python" -m pytest -q tests/test_colab_pilot.py tests/test_kramabench_data.py \
        tests/test_kramabench_adapter.py tests/test_kramabench_subprocess.py
    "$python" tests/fixtures/kramabench_compatibility_integration.py --kramabench-root "$KRAMABENCH_ROOT"
    echo "Offline checks passed."
}

math_checks() {
    bash -n bench/colab_pilot.sh
    "$python" -m pytest -q tests/test_conversation.py tests/test_scheduler.py
    "$python" -m bench.math_pilot --dataset "$math_dataset" --check "${math_selection[@]}"
    echo "Offline math checks passed."
}

case "$mode:$#" in
    setup:1)
        mkdir -p "$runtime"
        bash bench/setup_krama_checkout.sh --reuse
        "$python" -m pip install "pytest>=8,<10"
        offline_checks
        exit
        ;;
    check:1)
        offline_checks
        exit
        ;;
    math-setup:1|counterbench-setup:1)
        mkdir -p "$runtime"
        if [[ ! -x "$python" ]]; then
            "$host_python" -m venv --without-pip "$math_venv"
        fi
        if ! "$python" -I -m pip --version >/dev/null 2>&1; then
            "$host_python" -m pip --python "$python" install pip
        fi
        if [[ "$mode" == counterbench-setup ]]; then
            "$python" -m pip install "pydantic==2.14.1"
            "$python" -m bench.counterbench_pilot --setup
            exit
        fi
        "$python" -m pip install "pydantic==2.14.1" "pytest>=8,<10"
        if [[ ! -f "$math_dataset" ]]; then
            dataset_url="$("$python" -c 'from bench.math_pilot import DATASET_URL; print(DATASET_URL)')"
            curl -fsSL --retry 3 -o "$math_dataset.download" "$dataset_url"
            "$python" -m bench.math_pilot --dataset "$math_dataset.download" --check --tasks 1
            mv -- "$math_dataset.download" "$math_dataset"
        fi
        math_checks
        exit
        ;;
    math-check:1)
        math_checks
        exit
        ;;
    counterbench-run:1|counterbench-run:2)
        case "${2:-qwen3:4b-instruct-2507-q4_K_M}" in
            qwen3:4b-instruct-2507-q4_K_M|qwen3:8b|qwen3:14b-q4_K_M)
                export PILOT_MODEL="${2:-qwen3:4b-instruct-2507-q4_K_M}"
                ;;
            *) echo "Choose qwen3:4b-instruct-2507-q4_K_M, qwen3:8b, or qwen3:14b-q4_K_M." >&2; exit 2 ;;
        esac
        ;;
    run:2|math-run:*)
        if [[ $# -ne 2 ]]; then
            if [[ $# -lt 4 || "${3:-}" != --deadlocks-from ]]; then
                echo "Usage: bash bench/colab_pilot.sh math-run MODEL [--deadlocks-from LOG ...]" >&2
                exit 2
            fi
            math_selection=(--deadlocks-from "${@:4}")
        fi
        case "$2" in
            qwen3:8b|qwen3:14b-q4_K_M) export PILOT_MODEL="$2" ;;
            *) echo "Choose qwen3:8b or qwen3:14b-q4_K_M." >&2; exit 2 ;;
        esac
        ;;
    *) echo "Usage: bash bench/colab_pilot.sh {setup|check|run MODEL|math-setup|math-check|math-run MODEL|counterbench-setup|counterbench-run [MODEL]}" >&2; exit 2 ;;
esac

results_root="${COLAB_PILOT_RESULTS:-/content/blackboard-results}"
if [[ "$mode" == math-run ]]; then
    results_root="$results_root/math500"
elif [[ "$mode" == counterbench-run ]]; then
    results_root="$results_root/counterbench"
    if [[ ! -x "$python" ]]; then
        echo "Run bash bench/colab_pilot.sh counterbench-setup first." >&2
        exit 1
    fi
fi
mkdir -p "$results_root"
PILOT_RESULTS="$(mktemp -d "$results_root/${PILOT_MODEL//:/-}.XXXXXX")"
export PILOT_RESULTS TURNS=6 PILOT_TOOL_CALLS=12 PILOT_REQUEST_TIMEOUT_SECONDS=300
if [[ "$mode" == math-run || "$mode" == counterbench-run ]]; then
    exec > >(tee "$PILOT_RESULTS/progress.log") 2>&1
fi
echo "Results: $PILOT_RESULTS"
git rev-parse HEAD > "$PILOT_RESULTS/git_commit.txt"

echo "Checking the NVIDIA GPU..."
if [[ ! -d /content || "$(uname -m)" != x86_64 ]] || ! timeout 15s nvidia-smi -L; then
    echo "Choose an NVIDIA GPU in the Colab runtime before running a model." >&2
    exit 1
fi
if [[ "$mode" == math-run ]]; then
    echo "Checking the pinned math problems and conversation runner..."
    math_checks
elif [[ "$mode" == counterbench-run ]]; then
    "$python" -m bench.counterbench_pilot --setup
else
    offline_checks
fi

ollama_version=0.35.0
ollama_dir="$runtime/ollama-$ollama_version"
ollama="$ollama_dir/bin/ollama"
if [[ ! -f "$ollama_dir/.ready" ]]; then
    echo "Installing Ollama $ollama_version..."
    if ! command -v zstd >/dev/null; then
        apt-get update -qq
        apt-get install -y -qq zstd
    fi
    mkdir -p "$ollama_dir"
    archive="$runtime/ollama-linux-amd64.tar.zst"
    curl -fL --connect-timeout 20 --retry 3 -o "$archive" \
        "https://github.com/ollama/ollama/releases/download/v$ollama_version/ollama-linux-amd64.tar.zst"
    printf '%s  %s\n' '1c114a6b220c5efca2ef2b1e5f01d1e535e26f6cd6d1678c8489325d2835e525' "$archive" | sha256sum -c -
    tar --zstd -xf "$archive" -C "$ollama_dir"
    test -x "$ollama"
    touch "$ollama_dir/.ready"
    rm -- "$archive"
fi

export OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_MODELS="$runtime/models"
url="http://$OLLAMA_HOST"
if curl -fsS --max-time 2 "$url/api/version" >/dev/null 2>&1; then
    echo "Port 11434 already has an Ollama server. Stop it before starting this run." >&2
    exit 1
fi
echo "Starting Ollama..."
"$ollama" serve > "$PILOT_RESULTS/ollama.log" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true' EXIT
for attempt in {1..30}; do
    if curl -fsS --max-time 1 "$url/api/version" >/dev/null 2>&1; then break; fi
    if ! kill -0 "$server_pid" 2>/dev/null; then break; fi
    sleep 1
done
curl -fsS --max-time 3 "$url/api/version" > "$PILOT_RESULTS/ollama_version.json"
echo "Checking or downloading the $PILOT_MODEL weights..."
"$ollama" pull "$PILOT_MODEL"

echo "Loading $PILOT_MODEL and checking GPU placement..."
"$python" - "$PILOT_MODEL" "$PILOT_RESULTS/gpu.json" <<'PY'
import json
import sys
from pathlib import Path

from agents.llm_client import OPTIONS, OllamaClient

model, output = sys.argv[1:]
client = OllamaClient(model=model)
client.request("/api/generate", {
    "model": model, "prompt": "Say OK.", "think": False, "stream": False,
    "keep_alive": "10m", "options": {**OPTIONS, "num_predict": 1},
})
loaded = next((item for item in client.request("/api/ps")["models"] if item["name"] == model), None)
Path(output).write_text(json.dumps(loaded, indent=2) + "\n", encoding="utf-8")
if not loaded or not loaded["size"] or loaded["size_vram"] != loaded["size"] or loaded["context_length"] != OPTIONS["num_ctx"]:
    raise SystemExit("Ollama could not fully load the model on the GPU at the requested context. See gpu.json.")
print(f"{model}: fully on GPU, {loaded['context_length']} context tokens")
PY

if [[ "$mode" == math-run ]]; then
    "$python" -m bench.math_pilot --dataset "$math_dataset" --model "$PILOT_MODEL" \
        --output "$PILOT_RESULTS" "${math_selection[@]}"
elif [[ "$mode" == counterbench-run ]]; then
    "$python" -u -m bench.counterbench_pilot --model "$PILOT_MODEL" --output "$PILOT_RESULTS"
else
    status=0
    bash bench/baseline_pilot.sh || status=$?
    "$python" -m bench.monitor_runs "$PILOT_RESULTS" | tee "$PILOT_RESULTS/summary.jsonl"
    exit "$status"
fi
