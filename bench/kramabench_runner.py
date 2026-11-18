"""Run one selected KramaBench task through an explicit evaluator mode.

The benchmark checkout is read-only. Evaluation assets and all generated
results are staged under a fresh output directory outside that checkout.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agents.llm_client import MODEL, BASE_URL, OPTIONS, sanitized_base_url
from bench.kramabench_adapter import (
    DEFAULT_TASK_WALL_CLOCK_CAP_SECONDS, KRAMABENCH_REVISION, _prompt_version,
)
from bench.kramabench_preflight import WorkloadPreflightError, preflight_kramabench


SYSTEM_NAME = "KramaBenchPXPSystem"
OFFICIAL_EVALUATOR_MODE = "official"
COMPATIBILITY_EVALUATOR_MODE = "compatibility-no-key"
COMPATIBILITY_LOCAL_MODELS = (MODEL, "qwen3:8b")
EVALUATOR_MODES = (OFFICIAL_EVALUATOR_MODE, COMPATIBILITY_EVALUATOR_MODE)
COMPATIBILITY_PATCH_SHA256 = "39dc1ea82ad6d7a75ca3a18de7b3efcbeb58250b307b09c45541286d20394897"
COMPATIBILITY_EVALUATOR_PATH = "benchmark/evaluator.py"
COMPATIBILITY_ORIGINAL_LINE = b'        self.pipeline_evaluation_engine = GPTInterface(model="gpt-5-mini")\n'
COMPATIBILITY_PATCHED_LINE = (
    b'        self.pipeline_evaluation_engine = GPTInterface(model="gpt-5-mini") if evaluate_pipeline else None\n'
)
COMPATIBILITY_ALLOWLIST_PATH = Path(__file__).with_name("compat_allowlist.json")
AUDITED_DETERMINISTIC_METRICS = frozenset({
    "success", "mean_absolute_error", "mean_squared_error", "rae_score",
    "f1", "precision", "recall",
})
COMPATIBILITY_UNSAFE_METRICS = frozenset({"f1"})


class BenchmarkRevisionError(RuntimeError):
    """The supplied checkout is not the explicitly pinned benchmark revision."""


class OfficialHarnessError(RuntimeError):
    """The pinned official CLI failed or did not persist task evaluator output."""


PINNED_EVALUATOR_REQUIRED_FILES = (
    "evaluate.py",
    "benchmark/benchmark_api.py",
    "benchmark/benchmark.py",
    "benchmark/executor.py",
    "benchmark/evaluator.py",
    "benchmark/metrics.py",
    "benchmark/fixtures/answer_type_fixtures.json",
)


def _evaluator_source_fingerprint(benchmark_root: Path) -> str:
    """Reject local edits to the pinned evaluator and fingerprint its tracked tree."""
    try:
        listed = subprocess.run(
            ["git", "-C", str(benchmark_root), "ls-files", "-z", "--", "evaluate.py", "benchmark/"],
            check=True, capture_output=True, timeout=10,
        ).stdout.split(b"\0")
        tracked = sorted(item.decode("utf-8") for item in listed if item)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise BenchmarkRevisionError("cannot inspect tracked KramaBench evaluator sources") from exc

    missing = sorted(set(PINNED_EVALUATOR_REQUIRED_FILES) - set(tracked))
    if missing:
        raise BenchmarkRevisionError("pinned KramaBench evaluator sources are missing: " + ", ".join(missing))

    digest = hashlib.sha256()
    for relative in tracked:
        try:
            committed = subprocess.run(
                ["git", "-C", str(benchmark_root), "rev-parse", f"HEAD:{relative}"],
                check=True, capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            working = subprocess.run(
                ["git", "-C", str(benchmark_root), "hash-object", "--", relative],
                check=True, capture_output=True, text=True, timeout=10,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise BenchmarkRevisionError(f"cannot verify pinned evaluator source: {relative}") from exc
        if working != committed:
            raise BenchmarkRevisionError(
                f"KramaBench evaluator source is modified at {relative}; only the unmodified pinned evaluator is enabled."
            )
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(committed.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _git_blob_hash(benchmark_root: Path, relative_path: str) -> str:
    try:
        committed = subprocess.run(
            ["git", "-C", str(benchmark_root), "rev-parse", f"HEAD:{relative_path}"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        working = subprocess.run(
            ["git", "-C", str(benchmark_root), "hash-object", "--", relative_path],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise BenchmarkRevisionError(f"cannot verify pinned KramaBench input: {relative_path}") from exc
    if working != committed:
        raise BenchmarkRevisionError(f"pinned KramaBench input has local changes: {relative_path}")
    return committed


def _verify_compatibility_checkout(compatibility_root: Path) -> dict[str, str]:
    """Accept only the reviewed one-line patch on the exact pinned revision."""
    try:
        revision = subprocess.run(
            ["git", "-C", str(compatibility_root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        diff = subprocess.run(
            ["git", "-C", str(compatibility_root), "diff", "--binary", "--", COMPATIBILITY_EVALUATOR_PATH],
            check=True, capture_output=True, timeout=10,
        ).stdout
        changed = subprocess.run(
            ["git", "-C", str(compatibility_root), "diff", "--name-only"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.splitlines()
        status = subprocess.run(
            ["git", "-C", str(compatibility_root), "status", "--porcelain", "--untracked-files=all"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.splitlines()
        staged = subprocess.run(
            ["git", "-C", str(compatibility_root), "diff", "--cached", "--quiet"],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BenchmarkRevisionError("cannot verify isolated compatibility evaluator provenance") from exc

    if revision != KRAMABENCH_REVISION:
        raise BenchmarkRevisionError(
            f"compatibility evaluator revision is {revision}; required {KRAMABENCH_REVISION}"
        )
    patch_hash = hashlib.sha256(diff).hexdigest()
    if patch_hash != COMPATIBILITY_PATCH_SHA256:
        raise BenchmarkRevisionError(
            f"compatibility evaluator patch hash is {patch_hash}; expected the reviewed patch "
            f"{COMPATIBILITY_PATCH_SHA256}"
        )
    if changed != [COMPATIBILITY_EVALUATOR_PATH] or status != [f" M {COMPATIBILITY_EVALUATOR_PATH}"]:
        raise BenchmarkRevisionError("compatibility evaluator contains changes beyond the reviewed one-line patch")
    if staged.returncode != 0:
        raise BenchmarkRevisionError("compatibility evaluator has staged changes")
    source_hash = hashlib.sha256((compatibility_root / COMPATIBILITY_EVALUATOR_PATH).read_bytes()).hexdigest()
    return {
        "revision": revision,
        "patch_sha256": patch_hash,
        "patched_evaluator_sha256": source_hash,
    }


def _prepare_compatibility_checkout(source_root: Path, destination: Path) -> dict[str, str]:
    """Create a sparse, isolated evaluator copy and apply only the reviewed patch."""
    verify_benchmark_checkout(source_root)
    try:
        objects_path = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "--git-path", "objects"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        objects_root = Path(objects_path)
        if not objects_root.is_absolute():
            objects_root = source_root / objects_root
        objects_root = objects_root.resolve(strict=True)
        subprocess.run(["git", "init", "--quiet", str(destination)], check=True, capture_output=True, text=True, timeout=20)
        alternates = destination / ".git" / "objects" / "info" / "alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        alternates.write_text(str(objects_root) + "\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(destination), "update-ref", "refs/heads/kramabench-pinned", KRAMABENCH_REVISION],
            check=True, capture_output=True, text=True, timeout=10,
        )
        subprocess.run(
            ["git", "-C", str(destination), "sparse-checkout", "init", "--no-cone"],
            check=True, capture_output=True, text=True, timeout=20,
        )
        subprocess.run(
            ["git", "-C", str(destination), "sparse-checkout", "set", "--no-cone", "/evaluate.py", "/benchmark/"],
            check=True, capture_output=True, text=True, timeout=20,
        )
        subprocess.run(
            ["git", "-C", str(destination), "checkout", "--detach", KRAMABENCH_REVISION],
            check=True, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BenchmarkRevisionError("could not create the isolated pinned compatibility checkout") from exc

    evaluator_path = destination / COMPATIBILITY_EVALUATOR_PATH
    try:
        source = evaluator_path.read_bytes()
    except OSError as exc:
        raise BenchmarkRevisionError("pinned evaluator source is absent from isolated checkout") from exc
    if source.count(COMPATIBILITY_ORIGINAL_LINE) != 1:
        raise BenchmarkRevisionError("pinned evaluator no longer matches the reviewed compatibility patch context")
    evaluator_path.write_bytes(source.replace(COMPATIBILITY_ORIGINAL_LINE, COMPATIBILITY_PATCHED_LINE, 1))
    return _verify_compatibility_checkout(destination)


def _load_compatibility_allowlist(path: str | Path = COMPATIBILITY_ALLOWLIST_PATH) -> list[dict[str, Any]]:
    """Load the human-maintained compatibility allowlist without broad defaults."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkloadPreflightError("compatibility-no-key allowlist is missing or malformed") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise WorkloadPreflightError("compatibility-no-key allowlist has an unsupported schema")
    entries = document.get("entries")
    if not isinstance(entries, list):
        raise WorkloadPreflightError("compatibility-no-key allowlist entries must be a list")
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise WorkloadPreflightError("compatibility-no-key allowlist contains a malformed entry")
        workload = entry.get("workload")
        task_id = entry.get("task_id")
        key = (workload, task_id)
        if (
            not all(isinstance(value, str) and value for value in key)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", workload)
            or key in seen
        ):
            raise WorkloadPreflightError("compatibility-no-key allowlist has invalid or duplicate task identifiers")
        seen.add(key)
        inputs = entry.get("input_sha256")
        if not isinstance(inputs, dict) or not inputs:
            raise WorkloadPreflightError(f"compatibility allowlist entry {task_id!r} has no input hashes")
        for relative, digest in inputs.items():
            relative_path = Path(relative) if isinstance(relative, str) else None
            if (
                relative_path is None or relative_path.is_absolute() or ".." in relative_path.parts
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise WorkloadPreflightError(f"compatibility allowlist entry {task_id!r} has an invalid input hash")
        metric_names = entry.get("metric_names")
        if (
            not isinstance(metric_names, list) or not metric_names
            or any(not isinstance(name, str) or not name for name in metric_names)
            or len(set(metric_names)) != len(metric_names)
        ):
            raise WorkloadPreflightError(f"compatibility allowlist entry {task_id!r} has invalid metric names")
        if not isinstance(entry.get("answer_type"), str) or not entry["answer_type"]:
            raise WorkloadPreflightError(f"compatibility allowlist entry {task_id!r} has no answer type")
        if not isinstance(entry.get("workload_git_blob"), str) or not re.fullmatch(
            r"[0-9a-f]{40,64}", entry["workload_git_blob"]
        ):
            raise WorkloadPreflightError(f"compatibility allowlist entry {task_id!r} has no pinned workload blob")
        provenance = entry.get("evaluator_provenance")
        if not isinstance(provenance, dict):
            raise WorkloadPreflightError(f"compatibility allowlist entry {task_id!r} has no evaluator provenance")
    return entries


def _compatibility_allowlist_entry(workload: str, task_id: str,
                                   allowlist_path: str | Path = COMPATIBILITY_ALLOWLIST_PATH
                                   ) -> dict[str, Any]:
    entries = _load_compatibility_allowlist(allowlist_path)
    entry = next((item for item in entries
                  if item.get("workload") == workload and item.get("task_id") == task_id), None)
    if entry is None:
        raise WorkloadPreflightError(
            f"compatibility-no-key task is not explicitly allowlisted: {workload}/{task_id}"
        )
    return entry


def _validate_compatibility_task(root: Path, workload: str, task_id: str,
                                 dataset_directory: str | Path | None,
                                 model: str, base_url: str,
                                 run_subtasks: bool, enable_pipeline_evaluation: bool,
                                 summary: Any, selected_record: dict[str, Any],
                                 allowlist_path: str | Path = COMPATIBILITY_ALLOWLIST_PATH) -> dict[str, Any]:
    """Restrict the no-key route to a reviewed task, exact inputs, and deterministic metrics."""
    entry = _compatibility_allowlist_entry(workload, task_id, allowlist_path)
    if run_subtasks or enable_pipeline_evaluation:
        raise OfficialHarnessError(
            "compatibility-no-key requires pipeline evaluation and subtasks to remain disabled"
        )
    if model not in COMPATIBILITY_LOCAL_MODELS or base_url.rstrip("/") != BASE_URL:
        raise OfficialHarnessError(
            "compatibility-no-key requires one of the configured local models "
            f"{COMPATIBILITY_LOCAL_MODELS!r} at {BASE_URL!r}"
        )
    expected_dataset = (root / "data" / workload / "input").resolve()
    if dataset_directory is not None and Path(dataset_directory).resolve() != expected_dataset:
        raise WorkloadPreflightError("compatibility-no-key requires the pinned workload dataset directory")
    if summary.task_count != 1 or len(summary.tasks) != 1:
        raise WorkloadPreflightError("compatibility-no-key does not evaluate subtasks or multiple tasks")
    task = summary.tasks[0]
    if task.task_id != task_id or task.answer_type != entry.get("answer_type"):
        raise WorkloadPreflightError("compatibility-no-key task answer type differs from its allowlist entry")
    if selected_record.get("id") != task_id or selected_record.get("answer_type") != entry.get("answer_type"):
        raise WorkloadPreflightError("compatibility-no-key workload row differs from its allowlist entry")

    workload_relative = f"workload/{workload}.json"
    if _git_blob_hash(root, workload_relative) != entry.get("workload_git_blob"):
        raise BenchmarkRevisionError("pinned workload blob differs from the reviewed compatibility entry")

    actual_inputs: dict[str, str] = {}
    for source in task.permitted_files:
        path = source.get("path")
        digest = source.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str) or path in actual_inputs:
            raise WorkloadPreflightError("compatibility-no-key preflight returned invalid or duplicate input paths")
        actual_inputs[path] = digest
    if actual_inputs != entry.get("input_sha256"):
        raise WorkloadPreflightError("compatibility-no-key input paths or SHA-256 hashes differ from the allowlist")
    for relative in actual_inputs:
        pinned_input = (Path("data") / workload / "input" / relative).as_posix()
        _git_blob_hash(root, pinned_input)

    fixtures_path = root / "benchmark" / "fixtures" / "answer_type_fixtures.json"
    try:
        fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
        metric_names = next(item["metrics"] for item in fixtures if item.get("name") == task.answer_type)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        raise WorkloadPreflightError("cannot verify the pinned answer-type metric mapping") from exc
    if (
        not isinstance(metric_names, list)
        or not metric_names
        or any(not isinstance(name, str) or not name for name in metric_names)
    ):
        raise WorkloadPreflightError("pinned answer-type metric mapping is malformed")
    if metric_names != entry.get("metric_names"):
        raise WorkloadPreflightError("evaluator metric mapping differs from the compatibility allowlist entry")
    unreviewed = sorted(set(metric_names) - AUDITED_DETERMINISTIC_METRICS)
    if unreviewed:
        raise WorkloadPreflightError(
            "compatibility-no-key rejects non-deterministic or unaudited evaluator metrics: "
            + ", ".join(unreviewed)
        )
    unsafe_metrics = sorted(set(metric_names) & COMPATIBILITY_UNSAFE_METRICS)
    if unsafe_metrics:
        raise WorkloadPreflightError(
            "compatibility-no-key rejects evaluator metrics that may execute model-controlled code: "
            + ", ".join(unsafe_metrics)
        )
    if not metric_names:
        raise WorkloadPreflightError("compatibility-no-key requires at least one deterministic evaluator metric")
    provenance = entry.get("evaluator_provenance", {})
    if (
        provenance.get("revision") != KRAMABENCH_REVISION
        or provenance.get("patch_path") != COMPATIBILITY_EVALUATOR_PATH
        or provenance.get("patch_sha256") != COMPATIBILITY_PATCH_SHA256
    ):
        raise WorkloadPreflightError("compatibility allowlist entry has different evaluator patch provenance")
    return entry


def _python_command(python_executable: str | Path | None = None) -> str:
    requested = str(python_executable or sys.executable)
    resolved = shutil.which(requested)
    if resolved is None:
        raise OfficialHarnessError(f"evaluator Python executable is unavailable: {requested}")
    return resolved


def verify_evaluator_environment(python_executable: str | Path | None = None,
                                 additional_modules: tuple[str, ...] = (),
                                 require_openai_key: bool = True) -> str:
    """Check evaluator dependencies and optionally require key presence, never its value."""
    python = _python_command(python_executable)
    probe = (
        "import importlib.util,os,sys; "
        "names=('pandas','numpy','typeguard','rouge_score','nltk','openai','untruncate_json','ollama','pydantic')+"
        f"{additional_modules!r}; "
        "missing=[name for name in names if importlib.util.find_spec(name) is None]; "
        "print('MISSING='+','.join(missing)) if missing else None; "
        "sys.exit(2) if missing else None; "
        + (
            "sys.exit(3) if not os.environ.get('OPENAI_API_KEY') else print('READY')"
            if require_openai_key else "print('READY')"
        )
    )
    probe_environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if not require_openai_key:
        probe_environment.pop("OPENAI_API_KEY", None)
    try:
        completed = subprocess.run(
            [python, "-B", "-c", probe],
            env=probe_environment,
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OfficialHarnessError("could not inspect the configured evaluator Python environment") from exc
    if completed.returncode == 2:
        missing_line = next((line for line in completed.stdout.splitlines() if line.startswith("MISSING=")), "MISSING=unknown")
        raise OfficialHarnessError(
            "official KramaBench evaluator dependencies are missing: "
            + missing_line.removeprefix("MISSING=")
        )
    if require_openai_key and completed.returncode == 3:
        raise OfficialHarnessError(
            "OPENAI_API_KEY is not configured. The pinned Evaluator constructs GPTInterface even when "
            "pipeline evaluation is disabled; no key value is logged."
        )
    if completed.returncode != 0:
        raise OfficialHarnessError("configured evaluator Python failed its local dependency probe")
    return python


def verify_benchmark_checkout(root: str | Path) -> tuple[Path, Path]:
    """Require the requested upstream revision and its core evaluator files."""
    benchmark_root = Path(root).resolve(strict=True)
    if not benchmark_root.is_dir():
        raise BenchmarkRevisionError("KramaBench root is not a directory")
    required = (
        "evaluate.py",
        "benchmark/benchmark_api.py",
        "benchmark/benchmark.py",
        "benchmark/executor.py",
        "benchmark/evaluator.py",
        "benchmark/fixtures/answer_type_fixtures.json",
    )
    missing = [name for name in required if not (benchmark_root / name).is_file()]
    if missing:
        raise BenchmarkRevisionError(f"KramaBench checkout is missing required files: {', '.join(missing)}")
    try:
        completed = subprocess.run(
            ["git", "-C", str(benchmark_root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BenchmarkRevisionError("cannot read the KramaBench checkout revision") from exc
    actual_revision = completed.stdout.strip()
    if actual_revision != KRAMABENCH_REVISION:
        raise BenchmarkRevisionError(
            f"KramaBench checkout revision is {actual_revision}; required pinned revision is "
            f"{KRAMABENCH_REVISION}. No alternate revision will be substituted."
        )
    _evaluator_source_fingerprint(benchmark_root)
    evaluate_path = benchmark_root / "evaluate.py"
    return benchmark_root, evaluate_path


def verify_evaluator_cli(evaluate_path: Path, benchmark_root: Path,
                         arguments: list[str],
                         python_executable: str | Path | None = None,
                         no_openai_key: bool = False) -> None:
    """Ask the pinned evaluator's own argparse help which options it accepts."""
    probe_environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if no_openai_key:
        probe_environment.pop("OPENAI_API_KEY", None)
    try:
        completed = subprocess.run(
            [_python_command(python_executable), "-B", str(evaluate_path), "--help"],
            cwd=str(benchmark_root),
            env=probe_environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OfficialHarnessError("could not inspect the pinned evaluator CLI with --help") from exc
    help_text = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0:
        raise OfficialHarnessError(
            "the pinned evaluator did not complete its --help preflight "
            f"(exit {completed.returncode}): {help_text[-1200:]}"
        )
    requested_options = sorted({argument for argument in arguments if argument.startswith("--")})
    missing = [option for option in requested_options if option not in help_text]
    if missing:
        raise OfficialHarnessError(
            "the pinned evaluator --help does not advertise required options: " + ", ".join(missing)
        )


def verify_ollama_model(model: str, base_url: str = BASE_URL) -> dict[str, str]:
    """Check Ollama health and exact installed tag using GETs only."""
    if not isinstance(model, str) or not model.strip():
        raise OfficialHarnessError("configured Ollama model tag must be a nonempty string")
    endpoint = sanitized_base_url(base_url)
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/version", timeout=3) as response:
            version = json.load(response)
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=5) as response:
            tags = json.load(response)
    except (OSError, TimeoutError, ValueError) as exc:
        raise OfficialHarnessError(
            f"Ollama readiness check could not reach or read {endpoint}; no generation request was sent."
        ) from exc
    if not isinstance(version, dict) or not isinstance(tags, dict) or not isinstance(tags.get("models"), list):
        raise OfficialHarnessError("Ollama readiness response had an unexpected format")
    available = {
        item.get("name") or item.get("model")
        for item in tags["models"]
        if isinstance(item, dict)
    }
    if model not in available:
        raise OfficialHarnessError(
            f"configured Ollama model tag {model!r} is not listed at {endpoint}; no model was pulled."
        )
    return {"endpoint": endpoint, "version": str(version.get("version", "unknown")), "model": model}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


_DIAGNOSTIC_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?(?:openai[_-])?(?:api[_-]?key|access[_-]?token|secret|password)[\"']?\s*[:=]\s*[\"']?)"
    r"([^\s,;\"']+)([\"']?)"
)
_DIAGNOSTIC_BEARER = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/-]+=*")
_DIAGNOSTIC_OPENAI_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_DIAGNOSTIC_URL_CREDENTIALS = re.compile(r"(?i)(https?://)[^/@\s]+@")


def _redact_diagnostic_secrets(text: str) -> str:
    """Redact common credential forms before child diagnostics are persisted."""
    text = _DIAGNOSTIC_SECRET_ASSIGNMENT.sub(r"\1[REDACTED]\3", text)
    text = _DIAGNOSTIC_BEARER.sub(r"\1[REDACTED]", text)
    text = _DIAGNOSTIC_OPENAI_TOKEN.sub("[REDACTED]", text)
    return _DIAGNOSTIC_URL_CREDENTIALS.sub(r"\1[REDACTED]@", text)


def _persist_evaluator_failure_logs(run_root: Path, completed: Any) -> dict[str, Any]:
    """Persist captured child output without replacing files or exposing credentials."""
    run_root = Path(run_root)
    logs = {
        "stdout_log": (run_root / "evaluator_stdout.log", completed.stdout or ""),
        "stderr_log": (run_root / "evaluator_stderr.log", completed.stderr or ""),
    }
    for _, (path, content) in logs.items():
        file_descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(_redact_diagnostic_secrets(content))
    return {
        "exit_status": completed.returncode,
        "stdout_log": logs["stdout_log"][0].name,
        "stderr_log": logs["stderr_log"][0].name,
    }


def _task_failure_summary(run_root: Path, task_id: str) -> str | None:
    """Find a persisted adapter failure when the evaluator emitted no score row."""
    record_directory = Path(run_root) / "system_scratch" / SYSTEM_NAME / "krama_task_records"
    if not record_directory.is_dir():
        return None
    for path in sorted(record_directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("task_id") != task_id or record.get("status") != "failed":
            continue
        errors = record.get("errors", [])
        if not isinstance(errors, list):
            continue
        for error in errors:
            if not isinstance(error, dict):
                continue
            error_type = error.get("type")
            message = error.get("message")
            if isinstance(error_type, str) and isinstance(message, str) and message.strip():
                safe_message = _redact_diagnostic_secrets(message.strip())[:500]
                return f"{error_type}: {safe_message}"
    return None


def _run_provenance_metadata(summary: Any, task_id: str, evaluator_source_sha256: str,
                            model: str, max_turns: int, max_retries: int,
                            pipeline_evaluation_enabled: bool, run_subtasks: bool,
                            verbose: bool, ollama_readiness: dict[str, str],
                            evaluator_mode: str = OFFICIAL_EVALUATOR_MODE,
                            compatibility_info: dict[str, str] | None = None,
                            compatibility_allowlist_entry: dict[str, Any] | None = None,
                            model_seed: int = 42, num_ctx: int = OPTIONS["num_ctx"],
                            num_predict: int = OPTIONS["num_predict"]) -> dict[str, Any]:
    if evaluator_mode not in EVALUATOR_MODES:
        raise OfficialHarnessError(f"unsupported evaluator mode: {evaluator_mode}")
    if len(evaluator_source_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in evaluator_source_sha256):
        raise BenchmarkRevisionError("evaluator source fingerprint is missing or malformed")
    if evaluator_mode == COMPATIBILITY_EVALUATOR_MODE:
        if (
            compatibility_info is None
            or compatibility_info.get("revision") != KRAMABENCH_REVISION
            or compatibility_info.get("patch_sha256") != COMPATIBILITY_PATCH_SHA256
            or len(compatibility_info.get("patched_evaluator_sha256", "")) != 64
            or any(ch not in "0123456789abcdef" for ch in compatibility_info.get("patched_evaluator_sha256", ""))
            or compatibility_allowlist_entry is None
        ):
            raise BenchmarkRevisionError("compatibility evaluator provenance is missing or does not match the reviewed patch")
    elif compatibility_info is not None or compatibility_allowlist_entry is not None:
        raise BenchmarkRevisionError("official evaluator provenance cannot include a compatibility patch")

    task_summary = next(item for item in summary.tasks if item.task_id == task_id)
    prompt_version = _prompt_version()
    agent_config = {
        "model": model,
        "model_options": {**OPTIONS, "num_ctx": num_ctx, "num_predict": num_predict, "seed": model_seed},
        "max_turns": max_turns,
        "max_retries": max_retries,
        "pipeline_evaluation_enabled": pipeline_evaluation_enabled,
        "run_subtasks": run_subtasks,
    }
    config_sha256 = hashlib.sha256(
        json.dumps({"prompt_version": prompt_version, **agent_config}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    data_hashes = [
        {"task_id": item.task_id, "path": source["path"], "sha256": source["sha256"]}
        for item in summary.tasks
        for source in item.permitted_files
    ]
    metadata = {
        "run_type": "genuine_kramabench_task",
        "genuine_run_approved": True,
        "evaluator_mode": "official_unmodified" if evaluator_mode == OFFICIAL_EVALUATOR_MODE else evaluator_mode,
        "result_class": (
            "unmodified_official_evaluator_output"
            if evaluator_mode == OFFICIAL_EVALUATOR_MODE
            else "compatibility_evaluator_output"
        ),
        "evaluator_tree_fingerprint_sha256": evaluator_source_sha256,
        "evaluator_options": {
            "num_workers": 1,
            "use_truth_subset": True,
            "pipeline_evaluation_enabled": pipeline_evaluation_enabled,
            "run_subtasks": run_subtasks,
            "verbose": verbose,
        },
        "answer_type": task_summary.answer_type,
        "data_file_hashes": data_hashes,
        "prompt_version": prompt_version,
        "model_seed": model_seed,
        "agent_config_sha256": config_sha256,
        "ollama_readiness": ollama_readiness,
    }
    if compatibility_info is None:
        metadata["compatibility_patch"] = None
    else:
        metadata["evaluator_source_sha256"] = compatibility_info["patched_evaluator_sha256"]
        metadata["compatibility_patch"] = {
            "path": COMPATIBILITY_EVALUATOR_PATH,
            "sha256": compatibility_info["patch_sha256"],
        }
        metadata["openai_api_key_required"] = False
        metadata["compatibility_allowlist_entry_sha256"] = hashlib.sha256(
            json.dumps(compatibility_allowlist_entry, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    return metadata


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _stage_run_tree(run_root: Path, benchmark_root: Path, workload: str,
                    dataset_directory: Path, selected_tasks: list[dict[str, Any]]) -> None:
    (run_root / "workload").mkdir(parents=True)
    (run_root / "data" / workload).mkdir(parents=True)
    (run_root / "workload" / f"{workload}.json").write_text(
        json.dumps(selected_tasks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_root / "data" / workload / "input").symlink_to(dataset_directory.resolve(), target_is_directory=True)
    (run_root / "benchmark").symlink_to(benchmark_root / "benchmark", target_is_directory=True)


def _load_selected_records(workload_path: Path, task_ids: list[str]) -> list[dict[str, Any]]:
    # These full rows are staged only for the evaluator. The System receives
    # query/query_id/subset_files through the official Executor interface.
    rows = json.loads(workload_path.read_text(encoding="utf-8"))
    requested = set(task_ids)
    return [row for row in rows if row["id"] in requested]


def _require_no_answer_judge_when_pipeline_disabled(
    task_fixture_directory: Path, answer_type: str, pipeline_evaluation_enabled: bool
) -> None:
    if pipeline_evaluation_enabled:
        return
    fixture_path = task_fixture_directory / "answer_type_fixtures.json"
    answer_types = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture = next((item for item in answer_types if item.get("name") == answer_type), None)
    if fixture is None:
        raise WorkloadPreflightError(f"answer_type {answer_type!r} is absent from evaluator fixtures")
    llm_metrics = {"llm_paraphrase", "f1_approximate"}
    configured_metrics = set(fixture.get("metrics", []))
    if configured_metrics & llm_metrics:
        raise OfficialHarnessError(
            f"answer_type {answer_type!r} invokes an LLM evaluator metric even with pipeline evaluation "
            "disabled; choose a deterministic answer type or explicitly enable evaluator LLM calls."
        )


def _evaluator_result_namespace(evaluator_mode: str) -> str:
    if evaluator_mode == OFFICIAL_EVALUATOR_MODE:
        return "official_evaluator"
    if evaluator_mode == COMPATIBILITY_EVALUATOR_MODE:
        return "compatibility_evaluator"
    raise OfficialHarnessError(f"unsupported evaluator mode: {evaluator_mode}")


def _read_evaluator_measures(run_root: Path, workload: str) -> tuple[Path, Path, dict[str, list[dict[str, str]]]]:
    results_dir = run_root / "results" / SYSTEM_NAME
    measure_files = sorted(results_dir.glob(f"{workload}_measures_*.csv"))
    aggregate_file = run_root / "results" / "aggregated_results.csv"
    if not measure_files or not aggregate_file.is_file():
        raise OfficialHarnessError("selected KramaBench evaluator did not create task measures and aggregate output")
    grouped: dict[str, list[dict[str, str]]] = {}
    with measure_files[-1].open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            task_id = row.get("task_id")
            if task_id:
                grouped.setdefault(task_id, []).append(row)
    return measure_files[-1], aggregate_file, grouped


def run_one_task(benchmark_root: str | Path, workload: str, task_id: str,
                 output_directory: str | Path, dataset_directory: str | Path | None = None,
                 model: str = MODEL, base_url: str = BASE_URL,
                 max_turns: int = 6, max_retries: int = 1,
                 run_subtasks: bool = False,
                 enable_pipeline_evaluation: bool = False,
                 verbose: bool = False,
                 evaluator_python: str | Path | None = None,
                 approve_genuine_run: bool = False,
                 evaluator_mode: str = OFFICIAL_EVALUATOR_MODE,
                 task_wall_clock_cap_seconds: int = DEFAULT_TASK_WALL_CLOCK_CAP_SECONDS,
                 counterfactual_fraction: float = 0.0,
                 counterfactual_seed: int = 42,
                 counterfactual_streak_threshold: int = 2,
                 max_counterfactual_simulations: int = 1,
                 archive_label: str | None = None,
                 archive_driver_log: str | Path | None = None,
                 model_seed: int = 42, num_ctx: int = OPTIONS["num_ctx"],
                 num_predict: int = OPTIONS["num_predict"], required_num_ctx: int | None = None) -> Path:
    """Run one task through the explicitly selected, provenance-checked evaluator."""
    if approve_genuine_run is not True:
        raise OfficialHarnessError(
            "genuine evaluation is gated; pass --approve-genuine-run only after explicitly approving a real local model call."
        )
    if evaluator_mode not in EVALUATOR_MODES:
        raise OfficialHarnessError(f"unsupported evaluator mode: {evaluator_mode}")
    if type(task_wall_clock_cap_seconds) is not int or task_wall_clock_cap_seconds <= 0:
        raise OfficialHarnessError("task_wall_clock_cap_seconds must be a positive integer")
    if counterfactual_fraction not in (0, 0.33, 0.66, 1.0):
        raise OfficialHarnessError("counterfactual_fraction must be one of 0, 0.33, 0.66, or 1.0")
    if type(counterfactual_seed) is not int:
        raise OfficialHarnessError("counterfactual_seed must be an integer")
    if type(model_seed) is not int:
        raise OfficialHarnessError("model_seed must be an integer")
    if type(num_ctx) is not int or num_ctx <= 0:
        raise OfficialHarnessError("num_ctx must be a positive integer")
    if type(num_predict) is not int or num_predict <= 0 or num_predict > num_ctx:
        raise OfficialHarnessError("num_predict must be a positive integer no greater than num_ctx")
    required_num_ctx = num_ctx if required_num_ctx is None else required_num_ctx
    if type(required_num_ctx) is not int or required_num_ctx <= 0:
        raise OfficialHarnessError("required_num_ctx must be a positive integer")
    if num_ctx < required_num_ctx:
        raise OfficialHarnessError(
            f"effective Ollama num_ctx={num_ctx} is smaller than scope requirement {required_num_ctx}"
        )
    if type(counterfactual_streak_threshold) is not int or counterfactual_streak_threshold < 2:
        raise OfficialHarnessError("counterfactual_streak_threshold must be at least 2")
    if type(max_counterfactual_simulations) is not int or max_counterfactual_simulations < 0:
        raise OfficialHarnessError("max_counterfactual_simulations must be non-negative")
    root, evaluate_path = verify_benchmark_checkout(benchmark_root)
    evaluator_source_sha256 = _evaluator_source_fingerprint(root)
    workload_path = root / "workload" / f"{workload}.json"
    dataset_root = Path(dataset_directory).resolve() if dataset_directory else root / "data" / workload / "input"
    task_fixture_root = root / "benchmark" / "fixtures"
    summary = preflight_kramabench(
        workload_path, dataset_root, task_fixture_root,
        task_ids=[task_id], include_subtasks=run_subtasks,
    )
    if summary.task_count < 1 or task_id not in summary.requested_task_ids:
        raise WorkloadPreflightError("selected task was not validated")
    selected_records = _load_selected_records(workload_path, [task_id])
    if len(selected_records) != 1:
        raise WorkloadPreflightError("the selected task ID did not resolve to exactly one workload row")
    compatibility_allowlist_entry = None
    if evaluator_mode == COMPATIBILITY_EVALUATOR_MODE:
        compatibility_allowlist_entry = _validate_compatibility_task(
            root, workload, task_id, dataset_directory, model, base_url,
            run_subtasks, enable_pipeline_evaluation, summary, selected_records[0],
        )
    _require_no_answer_judge_when_pipeline_disabled(
        task_fixture_root,
        next(item.answer_type for item in summary.tasks if item.task_id == task_id),
        enable_pipeline_evaluation,
    )
    input_formats = {
        file["format"]
        for task in summary.tasks
        for file in task.permitted_files
    }
    format_modules = {"xlsx": "openpyxl", "pdf": "pdfplumber"}
    required_data_modules = tuple(sorted(format_modules[kind] for kind in input_formats if kind in format_modules))
    evaluator_python = verify_evaluator_environment(
        evaluator_python,
        required_data_modules,
        require_openai_key=(evaluator_mode == OFFICIAL_EVALUATOR_MODE),
    )

    cli_arguments = [
        "--sut", SYSTEM_NAME,
        "--workload", workload,
        "--project_root", "RUN_ROOT",
        "--result_directory", "results",
        "--task_fixtures", "benchmark/fixtures",
        "--num_workers", "1",
        "--use_truth_subset",
    ]
    if not enable_pipeline_evaluation:
        cli_arguments.append("--no_pipeline_eval")
    if run_subtasks:
        cli_arguments.append("--run_subtasks")
    if verbose:
        cli_arguments.append("--verbose")
    verify_evaluator_cli(
        evaluate_path, root, cli_arguments, evaluator_python,
        no_openai_key=(evaluator_mode == COMPATIBILITY_EVALUATOR_MODE),
    )
    ollama_readiness = verify_ollama_model(model, base_url)

    compatibility_temp = None
    compatibility_root: Path | None = None
    compatibility_info: dict[str, str] | None = None
    if evaluator_mode == COMPATIBILITY_EVALUATOR_MODE:
        compatibility_temp = tempfile.TemporaryDirectory(prefix="kramabench-compat-preflight-")
        compatibility_root = Path(compatibility_temp.name) / "checkout"
        try:
            compatibility_info = _prepare_compatibility_checkout(root, compatibility_root)
            verify_evaluator_cli(
                compatibility_root / "evaluate.py", compatibility_root,
                cli_arguments, evaluator_python, no_openai_key=True,
            )
        except BaseException:
            compatibility_temp.cleanup()
            raise

    try:
        provenance_metadata = _run_provenance_metadata(
            summary, task_id, evaluator_source_sha256, model, max_turns, max_retries,
            enable_pipeline_evaluation, run_subtasks, verbose, ollama_readiness,
            evaluator_mode=evaluator_mode, compatibility_info=compatibility_info,
            compatibility_allowlist_entry=compatibility_allowlist_entry,
            model_seed=model_seed,
            num_ctx=num_ctx, num_predict=num_predict,
        )
    except BaseException:
        if compatibility_temp is not None:
            compatibility_temp.cleanup()
        raise

    output_root = Path(output_directory).resolve()
    if _is_within(output_root, root):
        if compatibility_temp is not None:
            compatibility_temp.cleanup()
        raise ValueError("output_directory must be outside the read-only KramaBench checkout")
    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    run_root = output_root / run_id
    try:
        run_root.mkdir(parents=True, exist_ok=False)
    except BaseException:
        if compatibility_temp is not None:
            compatibility_temp.cleanup()
        raise
    manifest_path = run_root / "run_manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "benchmark_revision": KRAMABENCH_REVISION,
        "benchmark_checkout": str(root),
        "workload": workload,
        "task_id": task_id,
        "model_provider": "ollama",
        "evaluator_python": evaluator_python,
        "model": model,
        "base_url": sanitized_base_url(base_url),
        "model_options": {**OPTIONS, "num_ctx": num_ctx, "num_predict": num_predict, "seed": model_seed},
        "required_num_ctx": required_num_ctx,
        "model_seed": model_seed,
        "max_turns": max_turns,
        "max_retries": max_retries,
        "task_wall_clock_cap_seconds": task_wall_clock_cap_seconds,
        "counterfactual_fraction": float(counterfactual_fraction),
        "counterfactual_seed": counterfactual_seed,
        "counterfactual_streak_threshold": counterfactual_streak_threshold,
        "max_counterfactual_simulations": max_counterfactual_simulations,
        "num_workers": 1,
        "pipeline_evaluation_enabled": enable_pipeline_evaluation,
        "run_subtasks": run_subtasks,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "preflight": summary.to_jsonable(),
        "status": "staging",
    }
    manifest.update(provenance_metadata)
    try:
        _write_json(manifest_path, manifest)
        evaluator_root = root
        if compatibility_temp is not None and compatibility_root is not None:
            evaluator_root = run_root / "evaluator_compatibility_no_key"
            shutil.move(str(compatibility_root), evaluator_root)
            compatibility_temp.cleanup()
            compatibility_temp = None
            moved_compatibility_info = _verify_compatibility_checkout(evaluator_root)
            if moved_compatibility_info != compatibility_info:
                raise BenchmarkRevisionError(
                    "isolated compatibility evaluator changed after preflight; provenance does not match"
                )
        _stage_run_tree(run_root, evaluator_root, workload, dataset_root, selected_records)
        evaluate_path = evaluator_root / "evaluate.py"
        evaluator_arguments = [
            "--sut", SYSTEM_NAME,
            "--workload", workload,
            "--project_root", str(run_root),
            "--result_directory", "results",
            "--task_fixtures", "benchmark/fixtures",
            "--num_workers", "1",
            "--use_truth_subset",
        ]
        if not enable_pipeline_evaluation:
            evaluator_arguments.append("--no_pipeline_eval")
        if run_subtasks:
            evaluator_arguments.append("--run_subtasks")
        if verbose:
            evaluator_arguments.append("--verbose")

        project_root = Path(__file__).resolve().parents[1]
        subprocess_environment = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join(
                part for part in (str(project_root), os.environ.get("PYTHONPATH", "")) if part
            ),
            "KRAMABENCH_PROJECT_ROOT": str(project_root),
            "KRAMABENCH_GENUINE_RUN_APPROVED": "1",
            "KRAMABENCH_EVALUATOR_MODE": evaluator_mode,
            "KRAMABENCH_BENCHMARK_ROOT": str(root),
            "KRAMABENCH_COMPAT_PATCH_SHA256": (
                compatibility_info["patch_sha256"] if compatibility_info is not None else ""
            ),
            "KRAMABENCH_EVALUATE_PATH": str(evaluate_path),
            "KRAMABENCH_RUN_ROOT": str(run_root),
            "KRAMABENCH_EVALUATOR_ARGS": json.dumps(evaluator_arguments),
            "KRAMABENCH_WORKLOAD": workload,
            "KRAMABENCH_TASK_ID": task_id,
            "KRAMABENCH_MODEL": model,
            "KRAMABENCH_MODEL_SEED": str(model_seed),
            "KRAMABENCH_NUM_CTX": str(num_ctx),
            "KRAMABENCH_NUM_PREDICT": str(num_predict),
            "KRAMABENCH_REQUIRED_NUM_CTX": str(required_num_ctx),
            "KRAMABENCH_BASE_URL": base_url,
            "KRAMABENCH_MAX_TURNS": str(max_turns),
            "KRAMABENCH_MAX_RETRIES": str(max_retries),
            "KRAMABENCH_TASK_WALL_CLOCK_CAP_SECONDS": str(task_wall_clock_cap_seconds),
            "KRAMABENCH_COUNTERFACTUAL_FRACTION": str(counterfactual_fraction),
            "KRAMABENCH_COUNTERFACTUAL_SEED": str(counterfactual_seed),
            "KRAMABENCH_COUNTERFACTUAL_STREAK_THRESHOLD": str(counterfactual_streak_threshold),
            "KRAMABENCH_MAX_COUNTERFACTUAL_SIMULATIONS": str(max_counterfactual_simulations),
            "KRAMABENCH_PIPELINE_EVAL": "1" if enable_pipeline_evaluation else "0",
        }
        if evaluator_mode == COMPATIBILITY_EVALUATOR_MODE:
            subprocess_environment.pop("OPENAI_API_KEY", None)
        try:
            completed = subprocess.run(
                [evaluator_python, "-B", "-m", "bench.kramabench_subprocess"],
                cwd=str(run_root), env=subprocess_environment,
                capture_output=True, text=True, timeout=1800, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OfficialHarnessError("isolated KramaBench evaluator process could not complete") from exc
        driver_log_dir = run_root / "driver_logs"
        driver_log_dir.mkdir(parents=True, exist_ok=True)
        (driver_log_dir / "evaluator.stdout.log").write_text(completed.stdout or "", encoding="utf-8")
        (driver_log_dir / "evaluator.stderr.log").write_text(completed.stderr or "", encoding="utf-8")
        runtime_preflight_path = run_root / "model_runtime_preflight.json"
        if runtime_preflight_path.is_file():
            try:
                runtime_preflight = json.loads(runtime_preflight_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                runtime_preflight = {"status": "unreadable"}
            manifest["model_runtime_preflight"] = runtime_preflight
            effective_options = runtime_preflight.get("effective_options", {})
            if runtime_preflight.get("status") == "passed" and (
                not isinstance(effective_options, dict)
                or effective_options.get("num_ctx", 0) < required_num_ctx
            ):
                raise OfficialHarnessError(
                    "effective Ollama context reported by subprocess is below frozen-scope requirement"
                )
        else:
            manifest["model_runtime_preflight"] = {"status": "not_reached"}
        record_dir = run_root / "system_scratch" / SYSTEM_NAME / "krama_task_records"
        for record_path in record_dir.glob("*.json") if record_dir.is_dir() else []:
            try:
                task_record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if task_record.get("task_id") == task_id:
                actual_options = task_record.get("model_options")
                if isinstance(actual_options, dict):
                    manifest["effective_model_options"] = actual_options
                    if actual_options.get("num_ctx", 0) < required_num_ctx:
                        raise OfficialHarnessError(
                            "task record reports an effective Ollama context below frozen-scope requirement"
                        )
                manifest["data_loop_degraded"] = bool(task_record.get("data_loop_degraded", False))
                manifest["data_loop_degradation_reason"] = task_record.get("data_loop_degradation_reason")
                manifest["workflow_counters"] = task_record.get("workflow_counters", {})
                manifest["model_seed"] = task_record.get("model_seed", model_seed)
                break
        if completed.returncode != 0:
            process_failure = _persist_evaluator_failure_logs(run_root, completed)
            task_failure = _task_failure_summary(run_root, task_id)
            if task_failure:
                process_failure["task_failure_summary"] = task_failure
                failure_detail = f"adapter failed before producing a result for selected task {task_id} ({task_failure})"
            else:
                failure_detail = f"selected task {task_id} produced no evaluator result"
            manifest["evaluator_process"] = process_failure
            if not task_failure:
                manifest.update({"status": "no_result", "finished_at": datetime.now(timezone.utc).isoformat(),
                                 "result": "no result"})
                _write_json(manifest_path, manifest)
                return run_root
            _write_json(manifest_path, manifest)
            raise OfficialHarnessError(
                f"{failure_detail}; isolated evaluator exited with status {completed.returncode}. "
                f"Captured stdout: {process_failure['stdout_log']}; stderr: {process_failure['stderr_log']}. "
                "No score is accepted unless evaluator measure and aggregate files are present."
            )

        measures_path, aggregate_path, evaluator_rows = _read_evaluator_measures(run_root, workload)
        if task_id not in evaluator_rows:
            raise OfficialHarnessError(f"selected evaluator output has no row for selected task {task_id}")

        record_directory = run_root / "system_scratch" / SYSTEM_NAME / "krama_task_records"
        internal_records = []
        result_namespace = _evaluator_result_namespace(evaluator_mode)
        for path in sorted(record_directory.glob("*.json")) if record_directory.exists() else []:
            data = json.loads(path.read_text(encoding="utf-8"))
            data[f"{result_namespace}_output"] = evaluator_rows.get(data.get("task_id"), [])
            internal_records.append(data)
        if not any(record.get("task_id") == task_id for record in internal_records):
            raise OfficialHarnessError(f"adapter did not persist a task record for {task_id}")
        per_task_path = run_root / "per_task_evaluation_records.jsonl"
        per_task_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in internal_records),
            encoding="utf-8",
        )
        manifest.update({
            "status": "success",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            f"{result_namespace}_task_measures": str(measures_path),
            f"{result_namespace}_aggregate_measures": str(aggregate_path),
            "per_task_records": str(per_task_path),
        })
        _write_json(manifest_path, manifest)
        return run_root
    except BaseException as exc:
        manifest.update({
            "status": "failed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error_type": type(exc).__name__,
            "error": str(exc),
        })
        _write_json(manifest_path, manifest)
        raise
    finally:
        if archive_label is not None:
            from bench.archive_run import archive_run
            archive_run(
                run_root, archive_label,
                extra_files=[archive_driver_log] if archive_driver_log is not None else None,
            )
        if compatibility_temp is not None:
            compatibility_temp.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kramabench-root", required=True)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--task-id", required=True,
                        help="Exactly one official top-level task ID; pilot/full-study modes are not supported.")
    parser.add_argument("--dataset-directory")
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--model-seed", type=int, default=42,
                        help="Ollama generation seed; defaults to the existing seed (42).")
    parser.add_argument("--num-ctx", type=int, default=OPTIONS["num_ctx"],
                        help="Ollama context-window option.")
    parser.add_argument("--num-predict", type=int, default=OPTIONS["num_predict"],
                        help="Ollama maximum output tokens per generation.")
    parser.add_argument("--required-num-ctx", type=int,
                        help="Fail before generation if effective num_ctx is below this scope requirement.")
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--task-wall-clock-cap-seconds", type=int,
                        default=DEFAULT_TASK_WALL_CLOCK_CAP_SECONDS)
    parser.add_argument("--counterfactual-fraction", type=float, choices=(0, 0.33, 0.66, 1.0), default=0.0,
                        help="Seeded share of active agents enabled for counterfactual simulations.")
    parser.add_argument("--counterfactual-seed", type=int, default=42,
                        help="Seed used to choose counterfactually capable agents.")
    parser.add_argument("--counterfactual-streak-threshold", type=int, default=2,
                        help="Consecutive same-target REJECT/REFUTE events needed to trigger a simulation.")
    parser.add_argument("--max-counterfactual-simulations", type=int, default=1,
                        help="Maximum counterfactual proposal simulations for this task.")
    parser.add_argument("--archive-label",
                        help="Archive this run under archive/<label> after it finishes; labels are never overwritten.")
    parser.add_argument("--archive-driver-log",
                        help="Optional outer driver log to include in the archive.")
    parser.add_argument("--run-subtasks", action="store_true")
    parser.add_argument("--enable-pipeline-evaluation", action="store_true",
                        help="Allow upstream GPT evaluator calls, including pipeline and answer metrics.")
    parser.add_argument(
        "--evaluator-mode", choices=EVALUATOR_MODES, default=OFFICIAL_EVALUATOR_MODE,
        help="Evaluator implementation. compatibility-no-key accepts only explicitly allowlisted tasks.",
    )
    parser.add_argument(
        "--approve-genuine-run", action="store_true",
        help="Explicitly approve a genuine one-task run that will make local Ollama model calls.",
    )
    parser.add_argument(
        "--evaluator-python",
        default=os.environ.get("KRAMABENCH_EVALUATOR_PYTHON", sys.executable),
        help="Python executable containing the pinned KramaBench evaluator dependencies",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    try:
        run_root = run_one_task(
            benchmark_root=args.kramabench_root,
            workload=args.workload,
            task_id=args.task_id,
            output_directory=args.output_directory,
            dataset_directory=args.dataset_directory,
            model=args.model,
            model_seed=args.model_seed,
            num_ctx=args.num_ctx,
            num_predict=args.num_predict,
            required_num_ctx=args.required_num_ctx,
            base_url=args.base_url,
            max_turns=args.max_turns,
            max_retries=args.max_retries,
            run_subtasks=args.run_subtasks,
            enable_pipeline_evaluation=args.enable_pipeline_evaluation,
            verbose=args.verbose,
            evaluator_python=args.evaluator_python,
            approve_genuine_run=args.approve_genuine_run,
            evaluator_mode=args.evaluator_mode,
            task_wall_clock_cap_seconds=args.task_wall_clock_cap_seconds,
            counterfactual_fraction=args.counterfactual_fraction,
            counterfactual_seed=args.counterfactual_seed,
            counterfactual_streak_threshold=args.counterfactual_streak_threshold,
            max_counterfactual_simulations=args.max_counterfactual_simulations,
            archive_label=args.archive_label,
            archive_driver_log=args.archive_driver_log,
        )
    except Exception as exc:
        print(f"KramaBench run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.evaluator_mode == COMPATIBILITY_EVALUATOR_MODE:
        print(f"KramaBench compatibility-no-key output (not an unmodified official Evaluator result): {run_root}")
    else:
        print(f"KramaBench unmodified evaluator output: {run_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
