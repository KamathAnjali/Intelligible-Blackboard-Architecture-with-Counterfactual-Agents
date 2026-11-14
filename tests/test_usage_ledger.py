"""UsageLedger tests cover provider events, estimates, attribution, and persistence."""

import json

import pytest

from bench.metrics.token_counter import UsageLedger, UsageRecord
from bench.archive_run import archive_run
from bench.kramabench_adapter import _UsageCallbackClient
from bench.orchestrator import _run_usage_ledger
from ui.server import board_tap


def usage(**overrides):
    fields = {
        "session_id": "session-1", "task_id": "task-1", "agent_id": "proposer",
        "call_kind": "proposer", "branch_id": None, "prompt_tokens": 10,
        "completion_tokens": 4, "total_tokens": 14, "source": "provider",
        "model": "fixture-model", "started_at": "2026-11-11T00:00:00Z", "duration_ms": 20,
    }
    fields.update(overrides)
    return fields


def test_usage_ledger_aggregates_retries_self_checks_simulations_and_sources(tmp_path):
    ledger = UsageLedger()
    ledger.ingest(usage())
    ledger.ingest(usage(agent_id="verifier", call_kind="self_check", prompt_tokens=7,
                        completion_tokens=3, total_tokens=10))
    ledger.ingest(usage(agent_id="proposer", call_kind="retry", prompt_tokens=5,
                        completion_tokens=2, total_tokens=7))
    ledger.ingest(usage(agent_id="counterfactual", call_kind="counterfactual_sim",
                        branch_id="branch-1", prompt_tokens=9, completion_tokens=6, total_tokens=15))
    ledger.ingest(usage(agent_id="verifier", call_kind="other", source="estimated",
                        prompt_tokens=0, completion_tokens=8, total_tokens=8))

    report = ledger.export_report()
    assert report["total_calls"] == 5
    assert report["provider_total_tokens"] == 46
    assert report["estimated_total_tokens"] == 8
    assert report["total_tokens"] == sum(row["total_tokens"] for row in report["records"]) == 54
    assert report["estimated_share_pct"] == round(8 / 54 * 100, 2)
    assert report["agents"]["verifier"]["total_tokens"] == 18
    assert report["agents"]["proposer"]["call_kinds"]["retry"]["total_tokens"] == 7
    assert report["by_call_kind"]["self_check"]["total_tokens"] == 10
    assert report["by_execution"]["simulation"]["total_tokens"] == 15
    assert report["by_execution"]["live"]["total_tokens"] == 39

    artifact = ledger.write_json(tmp_path / "usage_ledgers" / "task.json")
    saved = json.loads(artifact.read_text())
    assert sum(row["total_tokens"] for row in saved["records"]) == saved["total_tokens"]
    archived = archive_run(tmp_path, "usage-ledger-test", repo_root=tmp_path / "repo")
    assert (archived / "usage_ledgers" / "task.json").exists()


def test_ledger_rejects_mismatched_totals_and_keeps_aggregates_unattributed():
    ledger = UsageLedger()
    with pytest.raises(ValueError, match="must equal"):
        ledger.ingest(usage(total_tokens=99))

    report = ledger.ingest_aggregate(session_id="session-2", task_id="task-2", source="provider",
                                     prompt_tokens=100, completion_tokens=25, calls=3)
    assert report["total_tokens"] == 125
    assert report["provider_total_tokens"] == 125
    assert report["call_attribution_available"] is False
    assert report["agents"]["unattributed"]["total_tokens"] == 125
    assert report["by_execution"]["unattributed"]["total_tokens"] == 125


def test_board_tap_fallback_labels_entry_text_estimate_and_dedupes(monkeypatch):
    board_tap.reset_token_usage("session-fallback", "task-fallback")
    event = {"type": "board_entry", "source": "LIVE", "session_id": "session-fallback",
             "task_id": "task-fallback", "entry": {
                 "entry_id": "entry-1", "agent_id": "proposer", "tag": "REVISE",
                 "prediction": "one two", "explanation": "three four",
             }}
    first = board_tap.process_board_entry_event(event)
    second = board_tap.process_board_entry_event(event)
    assert first["entry"]["token_count_kind"] == "estimated_board_entry_text_only"
    assert first["token_tally"]["measurement_label"] == "estimated, entry text only"
    assert first["token_tally"]["estimated_share_pct"] == 100.0
    assert second["token_tally"]["total_tokens"] == first["token_tally"]["total_tokens"]
    assert first["token_tally"]["provider_total_tokens"] == 0


def test_orchestrator_uses_saved_ledger_and_provider_aggregate_without_guessing_split():
    saved = UsageLedger()
    saved.ingest(usage())
    report = _run_usage_ledger({"usage_ledger": saved.export_report()}, None, "task-1")
    assert report["total_tokens"] == 14
    assert report["provider_total_tokens"] == 14
    assert report["call_attribution_available"] is True

    aggregate = _run_usage_ledger({
        "run_id": "run-1", "provider_token_usage": {
            "calls": 2, "prompt_tokens": 30, "output_tokens": 8,
        },
    }, None, "task-1")
    assert aggregate["total_tokens"] == 38
    assert aggregate["measurement_label"] == "provider-reported aggregate; per-call attribution unavailable"
    assert aggregate["by_execution"].get("live") is None
    assert aggregate["by_execution"]["unattributed"]["total_tokens"] == 38


def test_feature_detected_client_callback_adds_data_and_task_context():
    class CallbackClient:
        model = "fixture-model"

        def generate(self, prompt, *, on_usage=None):
            if on_usage:
                on_usage({
                    "prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15,
                    "source": "provider", "model": self.model,
                    "started_at": "2026-11-11T00:00:00Z", "duration_ms": 11,
                })
            return {"response": "{}"}

    events = []
    wrapped = _UsageCallbackClient(
        CallbackClient(), events.append, session_id="session-data", task_id="task-data",
        agent_id="data_agent", call_kind="other",
    )
    wrapped.generate("SYNTHETIC prompt")
    assert events == [{
        "session_id": "session-data", "task_id": "task-data", "agent_id": "data_agent",
        "call_kind": "other", "branch_id": None, "prompt_tokens": 12,
        "completion_tokens": 3, "total_tokens": 15, "source": "provider",
        "model": "fixture-model", "started_at": "2026-11-11T00:00:00Z", "duration_ms": 11,
    }]
