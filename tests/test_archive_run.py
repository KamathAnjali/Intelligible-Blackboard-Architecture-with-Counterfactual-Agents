import hashlib
import json

import pytest

from bench.archive_run import ArchiveError, archive_run


def test_archive_copies_artifacts_hashes_bytes_redacts_secrets_and_refuses_overwrite(tmp_path):
    source = tmp_path / "results"
    record_dir = source / "system_scratch" / "system" / "krama_task_records"
    logs = source / "driver_logs"
    measures = source / "results" / "system"
    record_dir.mkdir(parents=True)
    logs.mkdir()
    measures.mkdir(parents=True)
    (source / "run_manifest.json").write_text(
        json.dumps({"task_id": "generic", "OPENAI_API_KEY": "secret-value"}), encoding="utf-8"
    )
    (record_dir / "record.json").write_text(json.dumps({"password": "hidden"}), encoding="utf-8")
    (logs / "driver.log").write_text(
        "Authorization: Bearer token-value\nendpoint=http://user:password@host.local/path\n",
        encoding="utf-8",
    )
    measure = measures / "workload_measures_1.csv"
    measure.write_text("task_id,metric,value\ngeneric,success,1.0\n", encoding="utf-8")

    archived = archive_run(source, "unit-test", repo_root=tmp_path)
    assert (archived / "run_manifest.json").is_file()
    assert (archived / "system_scratch/system/krama_task_records/record.json").is_file()
    assert (archived / "driver_logs/driver.log").is_file()
    copied_measure = archived / "results/system/workload_measures_1.csv"
    assert copied_measure.read_text() == measure.read_text()
    assert "secret-value" not in (archived / "run_manifest.json").read_text()
    assert "hidden" not in (archived / "system_scratch/system/krama_task_records/record.json").read_text()
    assert "token-value" not in (archived / "driver_logs/driver.log").read_text()
    assert "user:password" not in (archived / "driver_logs/driver.log").read_text()

    manifest = json.loads((archived / "checksum_manifest.json").read_text())
    file_entry = next(item for item in manifest["files"] if item["path"] == "results/system/workload_measures_1.csv")
    assert file_entry["sha256"] == hashlib.sha256(copied_measure.read_bytes()).hexdigest()
    with pytest.raises(ArchiveError, match="will not be overwritten"):
        archive_run(source, "unit-test", repo_root=tmp_path)


def test_archive_rejects_path_traversal_and_empty_source(tmp_path):
    source = tmp_path / "empty"
    source.mkdir()
    with pytest.raises(ArchiveError, match="path-safe"):
        archive_run(source, "../escape", repo_root=tmp_path)
    with pytest.raises(ArchiveError, match="no recognized"):
        archive_run(source, "empty-archive", repo_root=tmp_path)
