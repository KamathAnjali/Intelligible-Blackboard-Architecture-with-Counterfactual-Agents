"""Archive selected benchmark artifacts with content hashes and secret redaction."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
_SECRET_KEY = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|secret|password|passwd|credential|authorization)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?(?:openai[_-])?(?:api[_-]?key|access[_-]?token|secret|password|passwd|credential|authorization)[\"']?\s*[:=]\s*[\"']?)([^\s,;\"']+)"
)
_BEARER = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_AUTH_SCHEME = re.compile(r"(?i)(Authorization\s*[:=]\s*)(?:Basic|Bearer)\s+[^\s,;]+")
_URL_USERINFO = re.compile(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@")


class ArchiveError(RuntimeError):
    """Raised when an archive cannot be created safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_label(label: str) -> str:
    if (not isinstance(label, str) or not label or label in {".", ".."}
            or Path(label).name != label or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in label)):
        raise ArchiveError("label must be a path-safe single directory name")
    return label


def _is_selected(path: Path) -> bool:
    name = path.name.casefold()
    parts = {part.casefold() for part in path.parts}
    if any(part in {".env", ".aws", ".ssh", "secrets", "credentials"} for part in parts):
        return False
    return (
        name == "run_manifest.json"
        or name == "transcript.json"
        or name == "per_task_evaluation_records.jsonl"
        or name == "aggregated_results.csv"
        or "krama_task_records" in parts and path.suffix.casefold() == ".json"
        or "usage_ledgers" in parts and path.suffix.casefold() == ".json"
        or path.suffix.casefold() == ".log"
        or ("driver_logs" in parts or "_driver_logs" in parts or "logs" in parts)
        and path.suffix.casefold() in {".txt", ".jsonl"}
        or "_measures_" in name and path.suffix.casefold() == ".csv"
    )


def _sanitize_text(text: str) -> str:
    text = _AUTH_SCHEME.sub(r"\1[REDACTED]", text)
    text = _BEARER.sub(r"\1[REDACTED]", text)
    text = _URL_USERINFO.sub(r"\1[REDACTED]@", text)
    text = _SECRET_ASSIGNMENT.sub(lambda match: match.group(1) + "[REDACTED]", text)
    return text


def _sanitize_json(text: str) -> str:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return _sanitize_text(text)

    def scrub(value):
        if isinstance(value, dict):
            return {
                key: "[REDACTED]" if _SECRET_KEY.search(str(key)) else scrub(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, str):
            return _sanitize_text(value)
        return value

    return json.dumps(scrub(data), ensure_ascii=False, indent=2) + "\n"


def archive_run(source: str | os.PathLike, label: str, *, repo_root: str | os.PathLike | None = None,
                extra_files: list[str | os.PathLike] | None = None) -> Path:
    """Copy selected run artifacts to ``archive/<label>`` without overwriting."""
    label = _safe_label(label)
    source_root = Path(source).resolve(strict=True)
    if not source_root.is_dir():
        raise ArchiveError("source must be a results directory")
    root = Path(repo_root).resolve() if repo_root is not None else REPO_ROOT
    archive_root = root / "archive"
    destination = archive_root / label
    archive_root.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ArchiveError(f"archive already exists and will not be overwritten: {destination}")

    candidates = []
    for path in source_root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            path.resolve(strict=True).relative_to(source_root)
        except ValueError:
            continue
        if _is_selected(path.relative_to(source_root)):
            candidates.append((path, path.relative_to(source_root)))
    for item in extra_files or []:
        path = Path(item).resolve(strict=True)
        if not path.is_file():
            raise ArchiveError(f"extra archive input is not a file: {path}")
        candidates.append((path, Path("driver_logs") / path.name))
    if not candidates:
        raise ArchiveError("source contains no recognized task records, manifests, logs, or measure CSVs")

    destination.mkdir(parents=False, exist_ok=False)
    archived = []
    try:
        for source_path, relative in candidates:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = source_path.read_text(encoding="utf-8", errors="replace")
            if source_path.suffix.casefold() in {".json", ".jsonl"} and source_path.suffix.casefold() == ".json":
                safe_text = _sanitize_json(raw)
            else:
                safe_text = _sanitize_text(raw)
            target.write_text(safe_text, encoding="utf-8")
            archived.append({
                "path": relative.as_posix(),
                "sha256": _sha256(target),
                "size_bytes": target.stat().st_size,
                "source_sha256": _sha256(source_path),
                "redaction_applied": safe_text != raw,
            })
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_directory": str(source_root),
            "label": label,
            "algorithm": "sha256",
            "files": archived,
            "note": "Checksums describe archived, secret-scrubbed bytes; this manifest is not self-hashed.",
        }
        (destination / "checksum_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Results root or single run directory")
    parser.add_argument("label", help="New archive/<label> directory; existing labels are never overwritten")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--extra-file", action="append", default=[],
                        help="Additional log file to archive under driver_logs/")
    args = parser.parse_args(argv)
    try:
        path = archive_run(args.source, args.label, repo_root=args.repo_root, extra_files=args.extra_file)
    except (OSError, ArchiveError) as exc:
        print(f"Archive failed: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
