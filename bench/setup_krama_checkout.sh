#!/usr/bin/env bash
set -euo pipefail

# Recreate the pinned upstream data/evaluator checkout without editing it.
# The compatibility-no-key evaluator patch is applied per run by
# kramabench_runner.py to a separate staged copy, never to this checkout.

readonly PINNED_REVISION="b2e0d77540263f8b6119f977fe79a8c4386b5a02"
readonly REPOSITORY_URL="https://github.com/mitdbg/KramaBench.git"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly CHECKOUT="${KRAMABENCH_ROOT:-${HOME}/kramabench-pinned}"
readonly EVALUATOR_VENV="${KRAMABENCH_EVALUATOR_VENV:-${HOME}/kramabench-eval-venv}"
readonly PYTHON="${PYTHON:-python3}"

case "${CHECKOUT}:${EVALUATOR_VENV}" in
  /private/tmp*|*/private/tmp*)
    echo "Refusing paths under /private/tmp; choose stable paths outside temporary storage." >&2
    exit 2
    ;;
esac

if [[ -e "${CHECKOUT}" ]]; then
  echo "Refusing to overwrite existing checkout: ${CHECKOUT}" >&2
  exit 2
fi
if [[ -e "${EVALUATOR_VENV}" ]]; then
  echo "Refusing to overwrite existing evaluator venv: ${EVALUATOR_VENV}" >&2
  exit 2
fi

git clone "${REPOSITORY_URL}" "${CHECKOUT}"
git -C "${CHECKOUT}" checkout --detach "${PINNED_REVISION}"

actual_revision="$(git -C "${CHECKOUT}" rev-parse HEAD)"
if [[ "${actual_revision}" != "${PINNED_REVISION}" ]]; then
  echo "Revision mismatch: expected ${PINNED_REVISION}, got ${actual_revision}" >&2
  exit 1
fi
if [[ -n "$(git -C "${CHECKOUT}" status --porcelain --untracked-files=all)" ]]; then
  echo "Fresh checkout is not clean: ${CHECKOUT}" >&2
  exit 1
fi

# Compare every allowlisted input to the exact committed blob and its recorded
# SHA-256. This validates both the repository object and the checked-out bytes.
"${PYTHON}" - "${PROJECT_ROOT}/bench/compat_allowlist.json" "${CHECKOUT}" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

allowlist_path, checkout_name = sys.argv[1:]
checkout = Path(checkout_name)
document = json.loads(Path(allowlist_path).read_text(encoding="utf-8"))
if document.get("schema_version") != 1 or not isinstance(document.get("entries"), list):
    raise SystemExit("Unsupported or malformed compatibility allowlist")

checked = 0
for entry in document["entries"]:
    workload = entry["workload"]
    for relative, expected_sha256 in entry["input_sha256"].items():
        repo_path = Path("data") / workload / "input" / relative
        path = checkout / repo_path
        if not path.is_file():
            raise SystemExit(f"Missing allowlisted input: {repo_path.as_posix()}")
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise SystemExit(f"Input SHA-256 mismatch: {repo_path.as_posix()}")
        blob = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", f"HEAD:{repo_path.as_posix()}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        working_blob = subprocess.run(
            ["git", "-C", str(checkout), "hash-object", "--", repo_path.as_posix()],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if blob != working_blob:
            raise SystemExit(f"Working-tree input differs from pinned Git blob: {repo_path.as_posix()}")
        checked += 1
print(f"Verified {checked} allowlisted input file hash(es).")
PY

"${PYTHON}" -m venv "${EVALUATOR_VENV}"
"${EVALUATOR_VENV}/bin/python" -m pip install --upgrade pip
"${EVALUATOR_VENV}/bin/python" -m pip install \
  "pandas==3.0.6" \
  "numpy==2.5.4" \
  "typeguard==4.6.0" \
  "rouge-score==0.1.2" \
  "nltk==3.10.3" \
  "openai==1.72.0" \
  "untruncate-json==1.1.0" \
  "ollama==0.6.3" \
  "pydantic==2.14.1" \
  "openpyxl==3.1.5" \
  "pdfplumber==0.11.6"

echo "Pinned KramaBench checkout: ${CHECKOUT} (${actual_revision})"
echo "Evaluator venv: ${EVALUATOR_VENV}"
echo "Compatibility patching remains isolated and automatic per runner invocation."
