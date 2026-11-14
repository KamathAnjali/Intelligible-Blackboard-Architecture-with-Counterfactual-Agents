"""Parser and feed tests; all marked examples are format/UI fixtures only."""

import asyncio
import json
from pathlib import Path

import pytest

from bench.ingest.krama_parser import (
    AgentFacingTask,
    LoadReport,
    load_krama_dataset,
    parse_krama_record,
    _run_cli,
)
from ui.server.event_bus import bus
from ui.server.fixtures.synthetic_feed import SYNTHETIC_SOURCE, synthetic_visualisation_feed


def test_agent_facing_type_excludes_reference_attributes_and_metadata_markers():
    parsed = parse_krama_record({
        "id": "SYNTHETIC_parser_fixture",
        "problem_statement": "SYNTHETIC placeholder question",
        "expected_answer": "SYNTHETIC_SECRET_GOLD",
        "ground_truth_reasoning_steps": ["SYNTHETIC_SECRET_STEP"],
        "metadata": {
            "answer_type": "numeric_exact",
            "nested": {"expected_answer": "SYNTHETIC_SECRET_NESTED", "reference_steps": ["hidden"]},
        },
    })
    agent_task = parsed.agent_facing_task
    assert isinstance(agent_task, AgentFacingTask)
    assert not hasattr(agent_task, "expected_answer")
    assert not hasattr(agent_task, "reference_steps")
    assert parsed.evaluator_reference.expected_answer == "SYNTHETIC_SECRET_GOLD"
    serialized = agent_task.model_dump_json()
    assert "SYNTHETIC_SECRET" not in serialized
    assert parsed.evaluator_reference.reference_steps == ["SYNTHETIC_SECRET_STEP"]


def test_loader_returns_and_persists_full_load_report(tmp_path):
    source = tmp_path / "SYNTHETIC_records.jsonl"
    source.write_text(
        '{"id":"SYNTHETIC_1","problem_statement":"first"}\n'
        'not-json\n'
        '{"id":"SYNTHETIC_2","problem_statement":"second"}\n',
        encoding="utf-8",
    )
    report_path = tmp_path / "load-report.json"
    result = load_krama_dataset(source, n=1, report_path=report_path)
    report = result.report
    assert isinstance(report, LoadReport)
    assert (report.n_total, report.n_loaded, report.n_rejected, report.n_limited_out) == (3, 1, 1, 1)
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["file_sha256"] == report.file_sha256
    assert saved["rejection_reasons"] == {"invalid_json": 1}


def test_parser_cli_prints_and_saves_sample_count(tmp_path, capsys):
    sample = Path(__file__).parents[1] / "bench" / "data" / "sample.jsonl"
    report_path = tmp_path / "SYNTHETIC_sample-report.json"
    assert _run_cli(["--input", str(sample), "--report-path", str(report_path)]) == 0
    output = capsys.readouterr().out
    assert "total=5 loaded=5 rejected=0" in output
    assert json.loads(report_path.read_text(encoding="utf-8"))["n_loaded"] == 5


def test_synthetic_visualisation_emits_only_labelled_gold_free_events(monkeypatch):
    events = []

    async def capture(event):
        events.append(event)

    monkeypatch.setattr(bus, "publish", capture)
    parsed = parse_krama_record({
        "id": "SYNTHETIC_feed_fixture",
        "problem_statement": "SYNTHETIC neutral sample prompt",
        "expected_answer": "SYNTHETIC_SECRET_GOLD",
        "ground_truth_reasoning_steps": ["SYNTHETIC_SECRET_STEP"],
    })
    states = synthetic_visualisation_feed([parsed.agent_facing_task], interval_s=0, emit_to_bus=True)
    assert len(states) == 1
    assert events and all(event["source"] == SYNTHETIC_SOURCE for event in events)
    assert all(event["source"] == "SYNTHETIC_FIXTURE" for event in events)
    assert "stream_complete" in [event["type"] for event in events]
    assert "SYNTHETIC_SECRET" not in json.dumps(events)
