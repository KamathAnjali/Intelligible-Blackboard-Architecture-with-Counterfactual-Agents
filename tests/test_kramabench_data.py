"""Synthetic tests for complete, allowlisted KramaBench data access."""

import json
import time
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from agents.llm_client import OllamaClient
from bench.kramabench_adapter import DatasetInputError, resolve_subset_files
from bench.kramabench_data import (
    MAX_GROUPS,
    MAX_TOOL_OUTPUT_CHARS,
    DataAccessError,
    DataOperationTimeout,
    DATA_TOOL_SYSTEM_PROMPT,
    DataToolRequest,
    DataToolFileSelectionError,
    DataToolWorkflowError,
    DatasetToolbox,
    gather_dataset_evidence,
    ollama_data_tool_schema,
)


def toolbox_for(root, patterns, **kwargs):
    return DatasetToolbox(root, resolve_subset_files(root, patterns), **kwargs)


def test_ollama_data_tool_schema_is_flat_and_preserves_structural_contract():
    schema = ollama_data_tool_schema()

    def walk(node):
        if isinstance(node, dict):
            assert not {"$defs", "$ref", "anyOf", "default", "title"}.intersection(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["action"]
    assert schema["properties"]["action"]["enum"] == [
        "catalog", "profile", "preview", "filter", "aggregate", "read_text",
        "search_text", "json_get", "done",
    ]
    assert schema["properties"]["filters"]["items"]["properties"]["operator"]["enum"] == [
        "eq", "ne", "contains", "gt", "gte", "lt", "lte", "is_null", "not_null",
    ]
    assert schema["properties"]["columns"]["type"] == ["array", "null"]
    assert schema["properties"]["columns"]["items"]["type"] == "string"
    assert schema["properties"]["filters"]["items"]["properties"]["value"]["type"] == [
        "string", "integer", "number", "null",
    ]


def test_precomputed_profiles_label_samples_and_exhaustive_low_cardinality_counts(tmp_path):
    path = tmp_path / "profile.csv"
    path.write_text("kind,amount\na,1\nb,2\na,3\n", encoding="utf-8")
    with toolbox_for(tmp_path, ["profile.csv"]) as toolbox:
        profiles = toolbox.precomputed_profiles()
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile["sha256"]
    table = profile["tables"][0]
    assert table["row_count"] == 3
    assert table["sample_rows"]["coverage"] == "sample"
    assert table["schema"][0]["dtype"].startswith("text")
    assert table["low_cardinality_counts"]["kind"]["coverage"] == "exhaustive"
    assert {item["value"]: item["count"] for item in table["low_cardinality_counts"]["kind"]["counts"]} == {
        "a": 2, "b": 1,
    }
    assert "amount" in table["low_cardinality_counts"]


@pytest.mark.parametrize("payload", [
    {},
    {"action": "preview", "offset": "not-an-integer"},
    {"action": "preview", "offset": "1"},
    {"action": "preview", "file": 7},
    {"action": "execute_sql"},
    {"action": "filter", "filters": [{"column": "x", "operator": "matches", "value": "a"}]},
    {"action": "preview", "limit": 51},
    {"action": "preview", "files": ["file.csv"] * 2_049},
    {"action": "preview", "columns": ["column"] * 2_001},
    {"action": "preview", "filters": [{"column": "x", "operator": "eq"}] * 33},
    {"action": "preview", "group_by": ["column"] * 4},
    {"action": "preview", "measures": [{"function": "count"}] * 33},
    {"action": "json_get", "path": ["key"] * 65},
    {"action": "read_text", "max_chars": 8_001},
    {"action": "done", "summary": "x" * 4_001},
    {"action": "preview", "extra": "field"},
])
def test_data_tool_request_rejects_missing_wrong_unknown_and_out_of_range_fields(payload):
    with pytest.raises(ValidationError):
        DataToolRequest.model_validate(payload, strict=True)


def test_aggregate_requires_unique_actionable_measures():
    with pytest.raises(ValidationError, match="aggregate requires at least one measure"):
        DataToolRequest.model_validate({"action": "aggregate"}, strict=True)
    with pytest.raises(ValidationError, match="remove redundant measures"):
        DataToolRequest.model_validate({
            "action": "aggregate",
            "measures": [
                {"function": "count", "column": "Violation"},
                {"function": "count", "column": "Violation", "name": "all_violations"},
            ],
        }, strict=True)
    with pytest.raises(ValidationError, match="aggregate does not accept `columns`; use `group_by`"):
        DataToolRequest.model_validate({
            "action": "aggregate",
            "columns": ["Violation"],
            "measures": [{"function": "count", "column": "*"}],
        }, strict=True)
    workflow_metadata = DataToolRequest.model_validate({
        "action": "aggregate",
        "file": "allowed.csv",
        "group_by": ["Violation"],
        "measures": [{"function": "count", "column": "*"}],
        "rate_outcome_column": "Violation",
    }, strict=True)
    assert workflow_metadata.action == "aggregate"
    assert workflow_metadata.rate_outcome_column == "Violation"
    assert "rate_outcome_column" in ollama_data_tool_schema()["properties"]


def test_ollama_client_sends_the_exact_data_tool_schema_without_network():
    client = OllamaClient()
    client.request = Mock(return_value={"response": '{"action":"done"}'})
    schema = ollama_data_tool_schema()

    client.generate("safe prompt", system="tool contract", schema=schema)

    path, payload = client.request.call_args.args
    assert path == "/api/generate"
    assert payload["format"] == schema
    assert payload["model"] == client.model
    assert payload["stream"] is False


def test_operation_deadline_is_fresh_after_model_generation_delay(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "allowed.csv").write_text("value\n7\n", encoding="utf-8")
    with toolbox_for(root, ["allowed.csv"]) as toolbox:
        toolbox.deadline = time.monotonic() - 10  # elapsed time belongs to the old, task-wide clock
        result = toolbox.execute({"action": "profile", "file": "allowed.csv"})
    assert result["row_count"] == 1


def test_timed_out_data_operation_is_not_retried_unchanged(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "allowed.csv").write_text("value\n7\n", encoding="utf-8")
    client = FakeDataToolClient([json.dumps({"action": "profile", "file": "allowed.csv"})])
    with toolbox_for(root, ["allowed.csv"]) as toolbox:
        toolbox.execute = Mock(side_effect=DataOperationTimeout("operation deadline reached"))
        with pytest.raises(DataToolWorkflowError, match="operation deadline reached"):
            gather_dataset_evidence(client, "Profile the table", toolbox, max_calls=2)
    assert len(client.calls) == 1
    toolbox.execute.assert_called_once()


class FakeDataToolClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def generate(self, prompt, system="", schema=None):
        self.calls.append({"prompt": prompt, "system": system, "schema": schema})
        return {"response": next(self.responses), "prompt_eval_count": 7, "eval_count": 3}


def test_gather_dataset_evidence_validates_requests_and_retries_malformed_json(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "allowed.csv").write_text("value\n7\n", encoding="utf-8")
    client = FakeDataToolClient([
        "not JSON",
        json.dumps({"action": "profile", "file": "allowed.csv"}),
        json.dumps({"action": "done", "summary": "Profiled the allowed file."}),
    ])

    with toolbox_for(root, ["allowed.csv"]) as toolbox:
        result = gather_dataset_evidence(client, "Profile the table", toolbox, max_calls=2)

    assert len(client.calls) == 3
    assert client.calls[0]["schema"] == ollama_data_tool_schema()
    assert "prior response did not satisfy" in client.calls[1]["system"]
    assert result["trace"][0]["action"] == "profile"
    assert result["usage"]["calls"] == 3
    assert result["request_diagnostics"][0]["status"] == "validation_error"
    assert result["request_diagnostics"][0]["validation_errors"]


def test_phase_29_file_identifier_in_files_field_recovers_and_profiles_filters(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    (root / "water-body-testing-2013.csv").write_text(
        "County Description,Year,Violation\nBarnstable,2013,yes\nPlymouth,2013,no\n",
        encoding="utf-8",
    )
    identifier = "water-body-testing-2013.csv"
    client = FakeDataToolClient([
        json.dumps({"action": "profile", "files": []}),
        json.dumps({"action": "profile", "files": [identifier], "table": "table_0"}),
        json.dumps({
            "action": "filter", "files": [identifier], "table": "table_0",
            "filters": [{"column": "County Description", "operator": "eq", "value": "Barnstable"}],
            "columns": ["County Description", "Violation"],
        }),
        json.dumps({"action": "done", "summary": "Profiled and filtered the allowlisted input."}),
    ])

    with toolbox_for(root, [identifier]) as toolbox:
        result = gather_dataset_evidence(client, "Inspect the selected environment data.", toolbox, max_calls=3)

    assert '"path": "water-body-testing-2013.csv"' in client.calls[0]["prompt"]
    observations = result["evidence"]["observations"]
    profile = observations[0]["result"]
    assert profile["file"] == identifier
    assert profile["row_count"] == 2
    filtered = observations[2]["result"]
    assert filtered["total_matches"] == 1
    assert filtered["rows"] == [{"County Description": "Barnstable", "Violation": "yes"}]
    assert [item["status"] for item in result["trace"]] == ["success", "success", "success"]
    assert result["trace"][0]["selector_defaulted"] is True
    assert result["trace"][1]["selector_defaulted"] is False
    assert result["workflow_diagnostics"]["workflow_counters"]["defaulted_file_selectors"] == 1
    assert result["usage"]["calls"] == 4
    assert result["workflow_diagnostics"]["tool_invocations"] == 3


def test_nested_allowlist_requires_exact_catalog_path_not_basename(tmp_path):
    root = tmp_path / "input"
    nested = root / "county-data"
    nested.mkdir(parents=True)
    (nested / "water-body-testing-2013.csv").write_text("value\n1\n", encoding="utf-8")

    with toolbox_for(root, ["county-data/water-body-testing-2013.csv"]) as toolbox:
        catalog_path = toolbox.catalog()["files"][0]["path"]
        assert catalog_path == "county-data/water-body-testing-2013.csv"
        with pytest.raises(DataToolFileSelectionError, match="invalid file path.*allowlisted path"):
            toolbox.execute({"action": "profile", "files": ["water-body-testing-2013.csv"]})
        assert toolbox.execute({"action": "profile", "files": [catalog_path]})["row_count"] == 1


def test_repeated_invalid_file_selection_stops_with_trace_and_usage(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "allowed.csv").write_text("value\n7\n", encoding="utf-8")
    invalid = json.dumps({"action": "profile", "files": ["private.csv"]})
    client = FakeDataToolClient([invalid, invalid, invalid])

    with toolbox_for(root, ["allowed.csv"]) as toolbox:
        execute = Mock(wraps=toolbox.execute)
        toolbox.execute = execute
        with pytest.raises(DataToolWorkflowError, match="repeated an invalid file path") as raised:
            gather_dataset_evidence(client, "Profile the permitted file", toolbox, max_calls=12)

    assert len(client.calls) == 2
    assert execute.call_count == 2
    assert raised.value.usage["calls"] == 2
    assert raised.value.workflow_diagnostics["tool_invocations"] == 2
    assert len(raised.value.trace) == 2
    assert all("invalid file path 'private.csv'" in item["error"] for item in raised.value.trace)


def test_recorded_missing_selectors_default_for_single_file_and_are_traced(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "one.csv").write_text("kind,value\na,2\nb,3\n", encoding="utf-8")
    requests = [
        {"action": "profile", "file": "one.csv"},
        {"action": "filter", "file": "one.csv", "filters": []},
        {"action": "aggregate", "file": "one.csv", "measures": [{"function": "count", "column": "*"}]},
        {"action": "filter", "filters": []},
        {"action": "filter", "files": [], "filters": []},
        {"action": "done", "summary": "Inspected the selected data."},
    ]
    client = FakeDataToolClient([json.dumps(request) for request in requests])

    with toolbox_for(root, ["one.csv"]) as toolbox:
        result = gather_dataset_evidence(client, "Inspect the selected data", toolbox, max_calls=5)

    assert [entry["status"] for entry in result["trace"]] == ["success"] * 5
    assert [entry["selector_defaulted"] for entry in result["trace"]] == [False, False, False, True, True]
    assert [entry["request"].get("file") for entry in result["trace"]] == ["one.csv"] * 5
    assert result["workflow_diagnostics"]["workflow_counters"]["defaulted_file_selectors"] == 2
    assert result["usage"]["calls"] == 6


def test_missing_selector_feedback_uses_last_successful_file_and_corrected_request_is_not_repeat(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "first.csv").write_text("value\n1\n", encoding="utf-8")
    (root / "second.csv").write_text("value\n2\n", encoding="utf-8")
    requests = [
        {"action": "profile", "file": "first.csv"},
        {"action": "preview"},
        {"action": "preview", "file": "first.csv"},
        {"action": "profile", "file": "second.csv"},
        {"action": "done", "summary": "Inspected both files."},
    ]
    client = FakeDataToolClient([json.dumps(request) for request in requests])

    with toolbox_for(root, ["first.csv", "second.csv"]) as toolbox:
        result = gather_dataset_evidence(client, "Inspect both selected files", toolbox, max_calls=4)

    assert [entry["status"] for entry in result["trace"]] == ["success", "error", "success", "success"]
    error = result["trace"][1]["error"]
    assert "`file` is missing" in error
    assert '"action": "preview", "file": "first.csv"' in error
    assert result["trace"][2]["request"]["file"] == "first.csv"
    assert result["workflow_diagnostics"]["workflow_counters"]["defaulted_file_selectors"] == 0


def test_repeated_missing_selector_reports_missing_file_not_invalid_path(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "first.csv").write_text("value\n1\n", encoding="utf-8")
    (root / "second.csv").write_text("value\n2\n", encoding="utf-8")
    missing = json.dumps({"action": "preview"})
    client = FakeDataToolClient([json.dumps({"action": "profile", "file": "first.csv"}), missing, missing])

    with toolbox_for(root, ["first.csv", "second.csv"]) as toolbox:
        with pytest.raises(DataToolWorkflowError, match="repeated a request with `file` missing") as raised:
            gather_dataset_evidence(client, "Inspect the selected files", toolbox, max_calls=5)

    assert len(raised.value.trace) == 3
    assert "`file` is missing" in raised.value.trace[-1]["error"]
    assert "invalid file path" not in raised.value.trace[-1]["error"]


def test_gather_dataset_evidence_stops_after_bounded_malformed_response_retries(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "allowed.csv").write_text("value\n7\n", encoding="utf-8")
    client = FakeDataToolClient(["{bad", "[]", "null", json.dumps({"action": "done"})])

    with toolbox_for(root, ["allowed.csv"]) as toolbox:
        with pytest.raises(DataToolWorkflowError, match="after 3 generation attempts") as raised:
            gather_dataset_evidence(client, "Profile the table", toolbox, max_calls=2)

    assert len(client.calls) == 3
    assert len(raised.value.request_diagnostics) == 3
    assert [item["generation_attempt"] for item in raised.value.request_diagnostics] == [1, 2, 3]


def test_invalid_structured_request_is_corrected_after_actionable_validation_feedback(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "allowed.csv").write_text("value\n7\n", encoding="utf-8")
    client = FakeDataToolClient([
        json.dumps({"action": "profile", "file": "allowed.csv", "limit": "50"}),
        json.dumps({"action": "profile", "file": "allowed.csv", "limit": 5}),
        json.dumps({"action": "done", "summary": "Profiled the permitted file."}),
    ])

    with toolbox_for(root, ["allowed.csv"]) as toolbox:
        result = gather_dataset_evidence(client, "Profile the table", toolbox, max_calls=2)

    assert len(client.calls) == 3
    assert "limit" in client.calls[1]["system"]
    assert result["trace"][0]["action"] == "profile"
    assert result["request_diagnostics"][0]["rejected_request"]
    assert result["request_diagnostics"][0]["validation_errors"][0]["field"] == "limit"


def test_identical_invalid_structured_responses_stop_early_with_bounded_diagnostics(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "allowed.csv").write_text("value\n7\n", encoding="utf-8")
    invalid = json.dumps({
        "action": "preview", "file": "allowed.csv", "unexpected": "field",
        "api_key": "sk-1234567890abcdefghijklmnopqrstuvwxyz",
    })
    client = FakeDataToolClient([invalid, invalid, invalid])

    with toolbox_for(root, ["allowed.csv"]) as toolbox:
        with pytest.raises(
            DataToolWorkflowError,
            match="repeated an identical invalid structured data request",
        ) as raised:
            gather_dataset_evidence(client, "Inspect the permitted file", toolbox, max_calls=2)

    assert len(client.calls) == 2
    error = raised.value
    assert error.usage["calls"] == 2
    assert error.workflow_diagnostics["generation_requests"] == 2
    assert len(error.request_diagnostics) == 2
    diagnostic = error.request_diagnostics[-1]
    assert diagnostic["repeated_identical_count"] == 2
    assert '"unexpected":"field"' in diagnostic["rejected_request"]
    assert "[REDACTED]" in diagnostic["rejected_request"]
    assert "sk-1234567890abcdefghijklmnopqrstuvwxyz" not in json.dumps(error.request_diagnostics)
    assert "api_key" in {item["field"] for item in diagnostic["validation_errors"]}


def test_tool_budget_allows_final_done_without_weakening_tool_cap(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "allowed.csv").write_text("value\n7\n", encoding="utf-8")
    client = FakeDataToolClient([
        json.dumps({"action": "profile", "file": "allowed.csv"}),
        json.dumps({"action": "profile", "file": "allowed.csv"}),
        json.dumps({"action": "done", "summary": "Finished after inspecting the allowed file."}),
    ])

    with toolbox_for(root, ["allowed.csv"]) as toolbox:
        result = gather_dataset_evidence(client, "Profile the table", toolbox, max_calls=2)

    assert result["usage"]["calls"] == 3
    assert result["usage"]["prompt_tokens"] == 21
    assert result["usage"]["output_tokens"] == 9
    assert result["workflow_diagnostics"] == {
        "max_tool_invocations": 2,
        "max_workflow_iterations": 4,
        "max_generation_requests": 12,
        "tool_invocations": 2,
        "workflow_iterations": 3,
        "generation_requests": 3,
        "automatic_recovery_invocations": 0,
        "blocked_done_attempts": 0,
        "category_inspection_pending": False,
        "unresolved_filter_categories": [],
        "rate_validation_required": False,
        "rate_outcome_column": None,
        "unresolved_outcome_categories": [],
        "duplicate_empty_filter_requests": 0,
        "invalid_structured_response_attempts": 0,
        "uninspected_task_files": [],
        "workflow_counters": {
            "guard_activations": [],
            "guard_retries": 0,
            "aggregate_columns_validation_feedback": 0,
            "multi_file_coverage_rejections": 0,
            "repeated_invalid_request_stops": 0,
            "blocked_done_rejections": 0,
            "duplicate_filter_rejections": 0,
            "defaulted_file_selectors": 0,
        },
        "completed": True,
    }
    assert len(result["trace"]) == 2


def test_tool_budget_still_rejects_another_operation_after_cap_with_diagnostics(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "allowed.csv").write_text("value\n7\n", encoding="utf-8")
    client = FakeDataToolClient([
        json.dumps({"action": "profile", "file": "allowed.csv"}),
        json.dumps({"action": "profile", "file": "allowed.csv"}),
        json.dumps({"action": "preview", "file": "allowed.csv"}),
    ])

    with toolbox_for(root, ["allowed.csv"]) as toolbox:
        execute = Mock(wraps=toolbox.execute)
        toolbox.execute = execute
        with pytest.raises(DataToolWorkflowError, match="2-tool-invocation limit") as raised:
            gather_dataset_evidence(client, "Profile the table", toolbox, max_calls=2)

    error = raised.value
    assert error.usage["calls"] == 3
    assert error.workflow_diagnostics["workflow_iterations"] == 3
    assert error.workflow_diagnostics["generation_requests"] == 3
    assert error.workflow_diagnostics["tool_invocations"] == 2
    assert error.workflow_diagnostics["completed"] is False
    assert len(error.attempts) == 3
    assert len(error.trace) == 3
    assert error.trace[-1]["action"] == "preview" and error.trace[-1]["status"] == "error"
    assert execute.call_count == 2


def test_oversized_full_table_preview_is_corrected_to_bounded_aggregate(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    row_count = 15_388
    rows = ["status"]
    rows.extend("exceeded" if index < 5_000 else "within" for index in range(row_count))
    (root / "samples.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    client = FakeDataToolClient([
        json.dumps({"action": "preview", "file": "samples.csv", "limit": row_count}),
        json.dumps({
            "action": "aggregate", "file": "samples.csv", "group_by": ["status"],
            "measures": [{"function": "count", "column": "*", "name": "samples"}],
        }),
        json.dumps({
            "action": "done", "rate_outcome_column": "status",
            "summary": "Counted the full table categories for numerator and denominator.",
        }),
    ])

    with toolbox_for(root, ["samples.csv"]) as toolbox:
        result = gather_dataset_evidence(client, "Calculate a percentage from the full table.", toolbox, max_calls=2)

    assert len(client.calls) == 3
    assert '"row_count": 15388' in client.calls[0]["prompt"]
    assert "limit" in client.calls[0]["system"] and "between 1 and 50" in client.calls[0]["system"]
    assert "limit: Input should be less than or equal to 50" in client.calls[1]["system"]
    assert "use `aggregate`" in client.calls[1]["system"]
    observation = result["evidence"]["observations"][0]
    assert observation["action"] == "aggregate"
    assert observation["result"]["matched_rows"] == row_count
    assert {row["status"]: row["samples"] for row in observation["result"]["groups"]} == {
        "exceeded": 5_000,
        "within": row_count - 5_000,
    }
    assert "rows" not in observation["result"]


def test_repeated_oversized_limit_is_rejected_without_executing_any_tool(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "samples.csv").write_text("value\n1\n", encoding="utf-8")
    oversized = json.dumps({"action": "preview", "file": "samples.csv", "limit": 15_388})
    client = FakeDataToolClient([oversized, oversized, oversized])

    with toolbox_for(root, ["samples.csv"]) as toolbox:
        execute = Mock(wraps=toolbox.execute)
        toolbox.execute = execute
        with pytest.raises(
            DataToolWorkflowError,
            match="repeated an identical invalid structured data request",
        ) as raised:
            gather_dataset_evidence(client, "Inspect the data", toolbox, max_calls=2)
        execute.assert_not_called()
    assert len(raised.value.request_diagnostics) == 2
    assert raised.value.request_diagnostics[0]["validation_errors"][0]["field"] == "limit"


def test_data_tool_generation_cannot_expand_the_file_allowlist(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "allowed.csv").write_text("value\n7\n", encoding="utf-8")
    (root / "private.csv").write_text("secret\nprivate contents\n", encoding="utf-8")
    client = FakeDataToolClient([
        json.dumps({"action": "preview", "file": "private.csv"}),
        json.dumps({"action": "profile", "file": "allowed.csv"}),
        json.dumps({"action": "done"}),
    ])

    with toolbox_for(root, ["allowed.csv"]) as toolbox:
        result = gather_dataset_evidence(client, "Inspect the allowed file", toolbox, max_calls=2)

    assert "allowed.csv" in client.calls[0]["prompt"]
    assert "private.csv" not in client.calls[0]["prompt"]
    assert "private contents" not in json.dumps(client.calls)
    assert result["evidence"]["observations"][0]["result"]["error"].startswith("invalid file path")


def make_text_pdf(path, text="Synthetic PDF total is 19"):
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode("ascii"))
        result.extend(body + b"\nendobj\n")
    xref_offset = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    result.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(result)


def test_large_csv_is_fully_indexed_and_paged_without_silent_row_loss(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    rows = ["id,value,note"]
    rows.extend(f"{index},{index},row-{index:05d}" for index in range(2_500))
    rows[-1] = "2499,2499,needle-at-end-of-full-input"
    source = root / "large.csv"
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert source.stat().st_size > 7_000

    with toolbox_for(root, ["large.csv"]) as toolbox:
        preview = toolbox.execute({"action": "preview", "file": "large.csv", "limit": 3})
        assert preview["total_rows"] == 2_500
        assert preview["has_more"] is True and preview["next_offset"] == 3
        complete = toolbox.execute({
            "action": "aggregate", "file": "large.csv",
            "measures": [{"function": "max", "column": "value", "name": "max_value"}],
        })
        assert complete["matched_rows"] == 2_500
        assert complete["groups"][0]["max_value"] == "2499"
        final_page = toolbox.execute({
            "action": "filter", "file": "large.csv", "offset": 0,
            "filters": [{"column": "note", "operator": "contains", "value": "needle-at-end"}],
        })
        assert final_page["total_matches"] == 1
        assert final_page["rows"][0]["id"] == "2499"


def test_csv_tsv_missing_values_and_full_aggregate(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "numbers.csv").write_text("group,value\na,2\na,\nb,3\n", encoding="utf-8")
    (root / "numbers.tsv").write_text("group\tvalue\na\t4\nb\t5\n", encoding="utf-8")
    with toolbox_for(root, ["numbers.csv", "numbers.tsv"]) as toolbox:
        csv_meta, tsv_meta = toolbox.catalog()["files"]
        assert csv_meta["format"] == "csv" and tsv_meta["format"] == "tsv"
        csv_result = toolbox.execute({
            "action": "aggregate", "file": "numbers.csv", "group_by": ["group"],
            "measures": [{"function": "sum", "column": "value", "name": "total"}],
        })
        assert {row["group"]: row["total"] for row in csv_result["groups"]} == {"a": "2", "b": "3"}
        missing = toolbox.execute({
            "action": "filter", "file": "numbers.csv",
            "filters": [{"column": "value", "operator": "is_null"}],
        })
        assert missing["total_matches"] == 1
        tsv_result = toolbox.execute({
            "action": "aggregate", "file": "numbers.tsv",
            "measures": [{"function": "sum", "column": "value"}],
        })
        assert tsv_result["groups"][0]["sum_value"] == "9"


def test_geographic_empty_filter_is_diagnostic_and_profiles_categorical_semantics(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "samples.csv").write_text(
        "County Description,Violation\nBarnstable,yes\nPlymouth,no\nEssex,yes\n",
        encoding="utf-8",
    )

    with toolbox_for(root, ["samples.csv"]) as toolbox:
        profile = toolbox.execute({"action": "profile", "file": "samples.csv"})
        empty = toolbox.execute({
            "action": "filter", "file": "samples.csv",
            "filters": [{
                "column": "County Description", "operator": "contains", "value": "Massachusetts",
            }],
        })

    assert profile["sample_values_note"].startswith("Each column has up to five non-null examples")
    assert empty["total_matches"] == 0 and empty["rows"] == []
    assert "not evidence of a zero count or rate" in empty["empty_result_diagnostic"]
    assert "County Description contains \"Massachusetts\"" in empty["empty_result_diagnostic"]
    assert empty["empty_filter_diagnostics"][0]["observed_values"] == [
        "Barnstable", "Essex", "Plymouth",
    ]
    assert empty["empty_filter_diagnostics"][0]["attempted_value"] == "Massachusetts"
    assert "county names, not state names" in DATA_TOOL_SYSTEM_PROMPT
    assert "explicitly count rows" in DATA_TOOL_SYSTEM_PROMPT
    assert "non-null count includes both `yes` and `no`" in DATA_TOOL_SYSTEM_PROMPT
    assert "rate_outcome_column" in DATA_TOOL_SYSTEM_PROMPT
    assert "group by the categorical outcome" in DATA_TOOL_SYSTEM_PROMPT
    assert "These samples are examples, not an exhaustive category inventory" in DATA_TOOL_SYSTEM_PROMPT
    assert "aggregate requires at least one measure" in DATA_TOOL_SYSTEM_PROMPT


def test_violation_yes_no_aggregate_counts_categories_with_bounded_output(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "samples.csv").write_text(
        "County Description,Violation\nBarnstable,yes\nPlymouth,no\nEssex,yes\nSuffolk,no\n",
        encoding="utf-8",
    )

    with toolbox_for(root, ["samples.csv"]) as toolbox:
        result = toolbox.execute({
            "action": "aggregate", "file": "samples.csv", "group_by": ["Violation"],
            "measures": [{"function": "count", "column": "*", "name": "records"}],
        })

    assert result["matched_rows"] == 4
    assert result["group_count"] == 2
    assert {row["Violation"]: row["records"] for row in result["groups"]} == {"yes": 2, "no": 2}
    assert len(json.dumps(result)) <= MAX_TOOL_OUTPUT_CHARS
    assert result["group_count"] <= MAX_GROUPS
    assert "rows" not in result


def test_rate_aggregate_counts_numerator_and_full_denominator_by_category(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "samples.csv").write_text(
        "County Description,Violation\nBarnstable,yes\nPlymouth,no\nEssex,yes\nSuffolk,no\n",
        encoding="utf-8",
    )
    with toolbox_for(root, ["samples.csv"]) as toolbox:
        result = toolbox.execute({
            "action": "aggregate", "file": "samples.csv", "group_by": ["Violation"],
            "measures": [{"function": "count", "column": "*", "name": "samples"}],
        })

    counts = {row["Violation"]: row["samples"] for row in result["groups"]}
    denominator = sum(counts.values())
    numerator = counts["yes"]
    assert result["matched_rows"] == denominator == 4
    assert counts == {"yes": 2, "no": 2}
    assert numerator / denominator * 100 == 50


def test_empty_filter_values_are_bounded_and_report_truncation(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    rows = ["County Description,Violation"]
    rows.extend(f"County-{index:02d},no" for index in range(11))
    (root / "samples.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    with toolbox_for(root, ["samples.csv"]) as toolbox:
        result = toolbox.execute({
            "action": "filter", "file": "samples.csv",
            "filters": [{"column": "County Description", "operator": "eq", "value": "NoSuchCounty"}],
        })
    diag = result["empty_filter_diagnostics"][0]
    assert len(diag["observed_values"]) == 8
    assert diag["values_truncated"] is True
    assert "NoSuchCounty" in result["empty_result_diagnostic"]
    assert len(json.dumps(result)) <= MAX_TOOL_OUTPUT_CHARS


def test_aggregate_rejects_category_cardinality_above_group_limit(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    rows = ["category"]
    rows.extend(f"category-{index}" for index in range(MAX_GROUPS + 1))
    (root / "many-categories.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    with toolbox_for(root, ["many-categories.csv"]) as toolbox:
        with pytest.raises(DataAccessError, match=f"aggregate exceeds {MAX_GROUPS} groups"):
            toolbox.execute({
                "action": "aggregate", "file": "many-categories.csv", "group_by": ["category"],
                "measures": [{"function": "count", "column": "*"}],
            })


def test_category_recovery_stops_when_existing_tool_budget_is_exhausted(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "samples.csv").write_text(
        "County Description,Violation\nBarnstable,yes\nPlymouth,no\nEssex,yes\n",
        encoding="utf-8",
    )
    client = FakeDataToolClient([
        json.dumps({
            "action": "filter", "file": "samples.csv",
            "filters": [{
                "column": "County Description", "operator": "contains", "value": "Massachusetts",
            }],
        }),
        json.dumps({
            "action": "done", "rate_outcome_column": "Violation",
            "summary": "The percentage is zero.",
        }),
        json.dumps({
            "action": "done", "rate_outcome_column": "Violation",
            "summary": "The same premature completion is repeated.",
        }),
    ])

    with toolbox_for(root, ["samples.csv"]) as toolbox:
        with pytest.raises(DataToolWorkflowError, match="budget is exhausted") as raised:
            gather_dataset_evidence(
                client,
                "What percentage of Massachusetts samples had a violation?",
                toolbox,
                max_calls=2,
            )

    assert len(client.calls) == 2
    assert "Violation" in raised.value.trace[-1]["error"]
    assert raised.value.workflow_diagnostics["blocked_done_attempts"] == 1
    assert raised.value.workflow_diagnostics["category_inspection_pending"] is True
    assert raised.value.workflow_diagnostics["rate_outcome_column"] == "Violation"
    assert raised.value.workflow_diagnostics["tool_invocations"] == 2
    assert raised.value.workflow_diagnostics["automatic_recovery_invocations"] == 1
    assert raised.value.trace[-2]["action"] == "aggregate_recovery"
    assert raised.value.trace[-2]["status"] == "success"
    assert raised.value.trace[-1]["action"] == "aggregate_recovery"
    assert raised.value.trace[-1]["status"] == "error"
    assert raised.value.workflow_diagnostics["unresolved_filter_categories"] == []
    assert raised.value.workflow_diagnostics["unresolved_outcome_categories"] == [
        {"file": "samples.csv", "table": "table_0", "column": "Violation"},
    ]
    assert raised.value.usage["calls"] == 2


def test_phase_212_premature_done_recovers_exact_categories_and_resumes_with_evidence(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    file_name = "water-body-testing-2013.csv"
    (root / file_name).write_text(
        "County Description,Violation\nBarnstable,yes\nPlymouth,no\nEssex,yes\nSuffolk,\n",
        encoding="utf-8",
    )
    client = FakeDataToolClient([
        json.dumps({"action": "profile", "file": file_name, "table": "table_0"}),
        json.dumps({
            "action": "filter", "file": file_name,
            "filters": [{
                "column": "County Description", "operator": "contains", "value": "Massachusetts",
            }],
        }),
        # These valid aggregates have no usable category grouping. The joint
        # grouping is also insufficient for a standalone outcome distribution.
        json.dumps({
            "action": "aggregate", "file": file_name, "rate_outcome_column": "Violation",
            "measures": [{"function": "count", "column": "*", "name": "records"}],
        }),
        json.dumps({
            "action": "aggregate", "file": file_name,
            "group_by": ["County Description", "Violation"],
            "measures": [{"function": "count", "column": "*", "name": "records"}],
        }),
        json.dumps({
            "action": "done", "rate_outcome_column": "Violation",
            "summary": "Cannot identify the requested geography from county categories. The full outcome distribution includes yes, no, and missing values, so the empty filter is not a zero rate.",
        }),
        # Reproduces the Phase 2.12 request shape. `columns` is not an aggregate
        # grouping and is rejected with actionable schema feedback.
        json.dumps({
            "action": "aggregate", "file": file_name, "columns": ["Violation"],
            "measures": [{"function": "count", "column": "*"}],
        }),
        json.dumps({
            "action": "done", "rate_outcome_column": "Violation",
            "summary": "Cannot calculate a geography-specific rate from the available evidence.",
        }),
    ])

    with toolbox_for(root, [file_name]) as toolbox:
        result = gather_dataset_evidence(
            client,
            "What percentage of Massachusetts samples had a violation?",
            toolbox,
            max_calls=6,
        )

    observations = result["evidence"]["observations"]
    assert observations[0]["action"] == "profile"
    assert observations[1]["result"]["total_matches"] == 0
    assert observations[4]["action"] == "workflow_validation"
    recovered = [item for item in observations if item["action"] == "aggregate_recovery"]
    assert len(recovered) == 2
    assert all(item["request"]["file"] == file_name for item in recovered)
    assert all(item["request"]["filters"] == [] for item in recovered)
    assert all(item["request"]["measures"] == [
        {"function": "count", "column": "*", "name": "category_count"},
    ] for item in recovered)
    assert [item["request"]["group_by"] for item in recovered] == [
        ["County Description"], ["Violation"],
    ]
    county_counts = recovered[0]["result"]["groups"]
    outcome_counts = recovered[1]["result"]["groups"]
    assert {row["County Description"] for row in county_counts} == {
        "Barnstable", "Plymouth", "Essex", "Suffolk",
    }
    assert {row["Violation"]: row["category_count"] for row in outcome_counts} == {
        "yes": 2, "no": 1, None: 1,
    }
    assert sum(row["category_count"] for row in outcome_counts) == 4
    assert "aggregate does not accept `columns`; use `group_by`" in client.calls[6]["system"]
    assert result["evidence"]["agent_summary"].startswith("Cannot calculate")
    assert result["usage"]["calls"] == 7
    assert result["workflow_diagnostics"]["tool_invocations"] == 6
    assert result["workflow_diagnostics"]["automatic_recovery_invocations"] == 2
    assert result["workflow_diagnostics"]["blocked_done_attempts"] == 1
    assert result["workflow_diagnostics"]["completed"] is True


def test_phase_210_trace_recovers_after_filter_and_aggregate_mistakes(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "water-body-testing-2013.csv").write_text(
        "County Description,Year,Violation\n"
        "Barnstable,2013,yes\nPlymouth,2013,no\nEssex,2013,yes\nSuffolk,2013,no\n",
        encoding="utf-8",
    )
    file_name = "water-body-testing-2013.csv"
    empty_filter = {
        "action": "filter", "file": file_name, "table": "table_0",
        "filters": [{"column": "County Description", "operator": "contains", "value": "Massachusetts"}],
    }
    client = FakeDataToolClient([
        json.dumps({"action": "profile", "file": file_name, "table": "table_0"}),
        json.dumps(empty_filter),
        json.dumps(empty_filter),  # Must be reported from cache, not executed again.
        json.dumps({
            "action": "aggregate", "file": file_name, "group_by": ["County Description"],
        }),  # Missing measures; the bounded schema retry below corrects it.
        json.dumps({
            "action": "aggregate", "file": file_name, "group_by": ["County Description"],
            "measures": [{"function": "count", "column": "*", "name": "samples"}],
        }),
        json.dumps({
            "action": "aggregate", "file": file_name, "group_by": ["County Description"],
            "measures": [{"function": "count", "column": "Violation", "name": "populated_violations"}],
        }),
        json.dumps({
            "action": "done", "rate_outcome_column": "Violation",
            "summary": "The rate is zero because the geographic filter returned no rows.",
        }),  # County counts alone do not establish Violation categories.
        json.dumps({
            "action": "aggregate", "file": file_name, "group_by": ["Violation"],
            "measures": [{"function": "count", "column": "*", "name": "samples"}],
        }),
        json.dumps({
            "action": "done", "rate_outcome_column": "Violation",
            "summary": "Used the unfiltered yes/no category counts for numerator and denominator.",
        }),
    ])

    with toolbox_for(root, [file_name]) as toolbox:
        filter_call = Mock(wraps=toolbox._filter)
        toolbox._filter = filter_call
        result = gather_dataset_evidence(
            client,
            "What percentage of water samples from Massachusetts beaches had a violation?",
            toolbox,
            max_calls=7,
        )

    assert filter_call.call_count == 1
    assert len(client.calls) == 9
    assert "aggregate requires at least one measure" in client.calls[4]["system"]
    assert "observed values" in client.calls[2]["prompt"]
    assert "Massachusetts" in client.calls[2]["prompt"]
    observations = result["evidence"]["observations"]
    empty_result = observations[1]["result"]
    assert empty_result["empty_filter_diagnostics"][0]["observed_values"] == [
        "Barnstable", "Essex", "Plymouth", "Suffolk",
    ]
    assert observations[2]["action"] == "workflow_validation"
    assert observations[2]["result"]["error"].startswith("this exact predicate already returned no rows")
    assert observations[5]["action"] == "workflow_validation"
    assert "outcome-category counts" in observations[5]["result"]["error"]
    category_counts = {
        row["Violation"]: row["category_count"]
        for row in observations[6]["result"]["groups"]
    }
    assert category_counts == {"yes": 2, "no": 2}
    denominator = sum(category_counts.values())
    assert category_counts["yes"] / denominator * 100 == 50
    assert result["workflow_diagnostics"]["completed"] is True


def test_premature_done_uses_deterministic_category_recovery_within_tool_budget(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "water-body-testing-2013.csv").write_text(
        "County Description,Year,Violation\n"
        "Barnstable,2013,yes\nPlymouth,2013,no\nEssex,2013,yes\nSuffolk,2013,no\n",
        encoding="utf-8",
    )
    file_name = "water-body-testing-2013.csv"
    county_filter = {
        "action": "filter", "file": file_name, "table": "table_0",
        "filters": [{"column": "County Description", "operator": "contains", "value": "Massachusetts"}],
    }
    client = FakeDataToolClient([
        json.dumps({"action": "profile", "file": file_name, "table": "table_0"}),
        json.dumps(county_filter),
        json.dumps(county_filter),
        json.dumps({"action": "done", "rate_outcome_column": "Violation", "summary": "Inspect categories."}),
        json.dumps({
            "action": "done", "rate_outcome_column": "Violation",
            "summary": "The empty filter is not a zero rate; the inspected county and outcome distributions are available in the evidence.",
        }),
    ])

    with toolbox_for(root, [file_name]) as toolbox:
        result = gather_dataset_evidence(
            client,
            "What percentage of Massachusetts samples had a violation?",
            toolbox,
            max_calls=5,
        )

    assert len(client.calls) == 5
    assert all(call["schema"] == ollama_data_tool_schema() for call in client.calls)
    assert not result["request_diagnostics"]
    observations = result["evidence"]["observations"]
    assert observations[1]["result"]["total_matches"] == 0
    assert observations[2]["action"] == "workflow_validation"
    assert "already returned no rows" in observations[2]["result"]["error"]
    recovery = [item for item in observations if item["action"] == "aggregate_recovery"]
    assert len(recovery) == 2
    assert all(item["request"]["filters"] == [] for item in recovery)
    outcome_counts = {row["Violation"]: row["category_count"] for row in recovery[1]["result"]["groups"]}
    assert outcome_counts == {"yes": 2, "no": 2}
    assert result["workflow_diagnostics"]["completed"] is True
    assert result["workflow_diagnostics"]["generation_requests"] == 5
    assert result["workflow_diagnostics"]["tool_invocations"] == 5
    assert result["workflow_diagnostics"]["automatic_recovery_invocations"] == 2
    assert result["workflow_diagnostics"]["blocked_done_attempts"] == 1
    assert result["workflow_diagnostics"]["duplicate_empty_filter_requests"] == 1


def test_verified_valid_empty_filter_can_finish_and_unrelated_empty_filter_is_not_a_gate(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "samples.csv").write_text("status,value\nopen,1\nactive,2\n", encoding="utf-8")

    relevant_client = FakeDataToolClient([
        json.dumps({
            "action": "filter", "file": "samples.csv",
            "filters": [{"column": "status", "operator": "eq", "value": "closed"}],
        }),
        json.dumps({
            "action": "aggregate", "file": "samples.csv", "group_by": ["status"],
            "measures": [{"function": "count", "column": "*", "name": "records"}],
        }),
        json.dumps({"action": "done", "summary": "No closed records; observed categories are open and active."}),
    ])
    with toolbox_for(root, ["samples.csv"]) as toolbox:
        valid_empty = gather_dataset_evidence(
            relevant_client, "Find records with status closed.", toolbox, max_calls=2,
        )
    assert valid_empty["workflow_diagnostics"]["completed"] is True
    assert valid_empty["workflow_diagnostics"]["unresolved_filter_categories"] == []

    exploratory_client = FakeDataToolClient([
        json.dumps({
            "action": "filter", "file": "samples.csv",
            "filters": [{"column": "status", "operator": "eq", "value": "closed"}],
        }),
        json.dumps({
            "action": "aggregate", "file": "samples.csv",
            "measures": [{"function": "count", "column": "*", "name": "records"}],
        }),
        json.dumps({"action": "done", "summary": "Counted all records; the exploratory filter was unrelated."}),
    ])
    with toolbox_for(root, ["samples.csv"]) as toolbox:
        exploratory = gather_dataset_evidence(
            exploratory_client, "Count all records in the table.", toolbox, max_calls=2,
        )
    assert exploratory["workflow_diagnostics"]["completed"] is True
    assert exploratory["workflow_diagnostics"]["category_inspection_pending"] is False


def test_empty_table_recovery_records_complete_empty_category_evidence(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "empty.csv").write_text("region,outcome\n", encoding="utf-8")
    client = FakeDataToolClient([
        json.dumps({"action": "profile", "file": "empty.csv"}),
        json.dumps({
            "action": "filter", "file": "empty.csv",
            "filters": [{"column": "region", "operator": "eq", "value": "north"}],
        }),
        json.dumps({
            "action": "done", "rate_outcome_column": "outcome",
            "summary": "The allowlisted table contains no rows, so there is no denominator and no rate can be calculated.",
        }),
        json.dumps({
            "action": "done", "rate_outcome_column": "outcome",
            "summary": "The category aggregates are empty; the dataset has no records.",
        }),
    ])

    with toolbox_for(root, ["empty.csv"]) as toolbox:
        result = gather_dataset_evidence(
            client,
            "What percentage of records in north had a positive outcome?",
            toolbox,
            max_calls=4,
        )

    recovery = [item for item in result["evidence"]["observations"] if item["action"] == "aggregate_recovery"]
    assert len(recovery) == 2
    assert all(item["result"]["matched_rows"] == 0 for item in recovery)
    assert all(item["result"]["groups"] == [] for item in recovery)
    assert result["workflow_diagnostics"]["automatic_recovery_invocations"] == 2
    assert result["workflow_diagnostics"]["unresolved_filter_categories"] == []
    assert result["workflow_diagnostics"]["unresolved_outcome_categories"] == []
    assert result["evidence"]["agent_summary"].startswith("The category aggregates are empty")


def test_empty_aggregate_recovery_checks_filter_and_outcome_separately(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "samples.csv").write_text(
        "County Description,Violation\nBarnstable,yes\nPlymouth,no\n",
        encoding="utf-8",
    )
    client = FakeDataToolClient([
        json.dumps({
            "action": "aggregate", "file": "samples.csv",
            "filters": [{
                "column": "County Description", "operator": "contains", "value": "Massachusetts",
            }],
            "measures": [{"function": "count", "column": "*", "name": "records"}],
        }),
        json.dumps({"action": "done", "summary": "The percentage is zero."}),
        json.dumps({
            "action": "aggregate", "file": "samples.csv", "group_by": ["County Description"],
            "measures": [{"function": "count", "column": "*", "name": "records"}],
        }),
        json.dumps({
            "action": "aggregate", "file": "samples.csv", "group_by": ["Violation"],
            "measures": [{"function": "count", "column": "*", "name": "records"}],
        }),
        json.dumps({
            "action": "done", "rate_outcome_column": "Violation",
            "summary": "Inspected county and outcome categories.",
        }),
    ])

    with toolbox_for(root, ["samples.csv"]) as toolbox:
        result = gather_dataset_evidence(
            client,
            "What percentage of Massachusetts samples had a violation?",
            toolbox,
            max_calls=5,
        )

    observations = result["evidence"]["observations"]
    assert observations[0]["result"]["matched_rows"] == 0
    assert "not evidence of a zero count or rate" in observations[0]["result"]["empty_result_diagnostic"]
    assert observations[1]["action"] == "workflow_validation"
    assert len(observations[2]["result"]["groups"]) == 2
    assert {row["Violation"]: row["records"] for row in observations[4]["result"]["groups"]} == {
        "yes": 1, "no": 1,
    }


def test_aggregate_combines_all_allowlisted_tables_with_matching_schema(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "part-a.csv").write_text("region,value\nwest,10\neast,2\n", encoding="utf-8")
    (root / "part-b.csv").write_text("region,value\nwest,3\neast,5\n", encoding="utf-8")
    with toolbox_for(root, ["part-a.csv", "part-b.csv"]) as toolbox:
        result = toolbox.execute({
            "action": "aggregate", "files": ["part-a.csv", "part-b.csv"],
            "group_by": ["region"],
            "measures": [{"function": "sum", "column": "value", "name": "total"}],
        })
        assert result["matched_rows"] == 4
        assert {row["region"]: row["total"] for row in result["groups"]} == {"west": "13", "east": "7"}


def test_json_and_jsonl_are_structured_and_json_paths_are_pageable(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "records.json").write_text(
        json.dumps({"items": [{"name": "a", "value": 5}, {"name": "b", "value": 8}], "meta": {"count": 2}}),
        encoding="utf-8",
    )
    (root / "records.jsonl").write_text('{"x":1}\n{"x":2}\n', encoding="utf-8")
    with toolbox_for(root, ["records.json", "records.jsonl"]) as toolbox:
        nested_table = toolbox.catalog()["files"][0]["tables"][0]["name"]
        aggregate = toolbox.execute({
            "action": "aggregate", "file": "records.json", "table": nested_table,
            "measures": [{"function": "sum", "column": "value"}],
        })
        assert aggregate["groups"][0]["sum_value"] == "13"
        json_page = toolbox.execute({
            "action": "json_get", "file": "records.json", "path": ["items"], "limit": 1,
        })
        assert json_page["value"] == [{"name": "a", "value": 5}]
        assert json_page["has_more"] is True and json_page["next_offset"] == 1
        jsonl_total = toolbox.execute({
            "action": "aggregate", "file": "records.jsonl",
            "measures": [{"function": "sum", "column": "x"}],
        })
        assert jsonl_total["groups"][0]["sum_x"] == "3"


def test_text_encoding_search_and_contiguous_pages(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "notes.txt").write_bytes("caf\xe9\nSecond useful line\n".encode("cp1252"))
    (root / "utf16.txt").write_bytes("No BOM UTF-16 text".encode("utf-16-le"))
    with toolbox_for(root, ["notes.txt", "utf16.txt"]) as toolbox:
        info = toolbox.catalog()["files"][0]
        assert info["path"] == "notes.txt"
        assert info["encoding"] == "cp1252"
        first = toolbox.execute({"action": "read_text", "file": "notes.txt", "offset": 0, "max_chars": 5})
        second = toolbox.execute({"action": "read_text", "file": "notes.txt", "offset": first["next_offset"], "max_chars": 100})
        assert first["text"] + second["text"] == "café\nSecond useful line\n"
        hits = toolbox.execute({"action": "search_text", "file": "notes.txt", "text": "useful"})
        assert hits["total_hits"] == 1 and hits["matches"][0]["line"] == 2
        utf16 = toolbox.execute({"action": "read_text", "file": "utf16.txt", "max_chars": 100})
        assert utf16["text"] == "No BOM UTF-16 text"
        assert toolbox.files["utf16.txt"]["encoding"] == "utf-16-le"


def test_xlsx_all_sheets_and_pdf_text_are_accessible(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    pytest.importorskip("pdfplumber")

    root = tmp_path / "dataset"
    root.mkdir()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sales"
    sheet.append(["region", "amount"])
    sheet.append(["west", 10])
    sheet.append(["east", 12])
    sheet.append(["missing", None])
    hidden = workbook.create_sheet("Hidden source")
    hidden.append(["value"])
    hidden.append([99])
    hidden.sheet_state = "hidden"
    workbook.save(root / "sales.xlsx")
    make_text_pdf(root / "report.pdf")

    with toolbox_for(root, ["sales.xlsx", "report.pdf"]) as toolbox:
        files = {item["path"]: item for item in toolbox.catalog()["files"]}
        assert {table["name"] for table in files["sales.xlsx"]["tables"]} == {"Sales", "Hidden source"}
        sales_sum = toolbox.execute({
            "action": "aggregate", "file": "sales.xlsx", "table": "Sales",
            "measures": [{"function": "sum", "column": "amount"}],
        })
        assert sales_sum["groups"][0]["sum_amount"] == "22"
        assert files["report.pdf"]["page_count"] == 1
        text = toolbox.execute({"action": "search_text", "file": "report.pdf", "text": "total"})
        assert text["total_hits"] == 1 and text["matches"][0]["page"] == 1


def test_html_static_table_is_queryable_and_text_remains_accessible(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "source.html").write_text(
        "<html><table><tr><th>state</th><th>count</th></tr>"
        "<tr><td>North</td><td>4</td></tr><tr><td>South</td><td>6</td></tr></table></html>",
        encoding="utf-8",
    )
    with toolbox_for(root, ["source.html"]) as toolbox:
        meta = toolbox.catalog()["files"][0]
        assert meta["tables"][0]["row_count"] == 2
        assert meta["tables"][0]["columns"] == ["state", "count"]
        result = toolbox.execute({
            "action": "aggregate", "file": "source.html",
            "measures": [{"function": "sum", "column": "count"}],
        })
        assert result["groups"][0]["sum_count"] == "10"
        assert toolbox.execute({"action": "search_text", "file": "source.html", "text": "North"})["total_hits"] == 1


def test_delimited_parser_skips_and_reports_key_value_metadata_before_quoted_header(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "metadata.csv").write_text(
        'Title: Monthly report\nMissing:-999\n\nDate,"Acres, total",Count\n'
        "202401,12.5,2\n202402,8,1\n",
        encoding="utf-8",
    )

    with toolbox_for(root, ["metadata.csv"]) as toolbox:
        descriptor = toolbox.catalog()["files"][0]
        assert descriptor["tables"][0]["columns"] == ["Date", "Acres, total", "Count"]
        assert descriptor["tables"][0]["row_count"] == 2
        assert any(
            "Skipped 2 leading key:value metadata row(s) (Title, Missing)" in note
            and "physical line(s) 1, 2" in note
            and "header at physical line 4" in note
            for note in descriptor["notes"]
        )


@pytest.mark.parametrize(
    ("filename", "contents", "message"),
    [
        ("bad.csv", "a,b\n1,2,3\n", "extra columns"),
        ("bad.json", "{bad", "malformed JSON"),
    ],
)
def test_malformed_inputs_fail_explicitly(tmp_path, filename, contents, message):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / filename).write_text(contents, encoding="utf-8")
    with pytest.raises(DataAccessError, match=message):
        toolbox_for(root, [filename])


def test_missing_files_encoding_and_timeout_fail_without_partial_results(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "records.csv").write_text("value\n7\n", encoding="utf-8")
    with pytest.raises(DatasetInputError, match="no dataset files match"):
        resolve_subset_files(root, ["missing.csv"])
    with pytest.raises(DataAccessError, match="positive integer"):
        toolbox_for(root, ["records.csv"], timeout_seconds=0)
    with toolbox_for(root, ["records.csv"]) as toolbox:
        toolbox.deadline = time.monotonic() - 1
        assert toolbox.execute({"action": "profile", "file": "records.csv"})["row_count"] == 1


def test_aggregate_columns_failure_returns_allowlisted_corrected_example_and_stays_strict(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "records.csv").write_text("group,value\na,2\nb,4\n", encoding="utf-8")
    rejected = json.dumps({
        "action": "aggregate", "file": "records.csv", "table": "table_0",
        "columns": ["value"], "measures": [{"function": "mean", "column": "value"}],
    }, separators=(",", ":"))
    corrected = json.dumps({
        "action": "aggregate", "file": "records.csv", "table": "table_0",
        "group_by": [], "measures": [{"function": "mean", "column": "value"}],
    })
    client = FakeDataToolClient([
        rejected,
        corrected,
        json.dumps({"action": "done", "summary": "The aggregate reports the mean."}),
    ])

    with toolbox_for(root, ["records.csv"]) as toolbox:
        with pytest.raises(ValidationError, match="aggregate does not accept `columns`"):
            DataToolRequest.model_validate_json(rejected, strict=True)
        result = gather_dataset_evidence(client, "Find the mean value.", toolbox, max_calls=2)

    feedback = client.calls[1]["system"]
    assert '"file":"records.csv"' in feedback
    assert '"table":"table_0"' in feedback
    assert '"action":"aggregate"' in feedback
    example_line = next(line for line in feedback.splitlines() if "Corrected example" in line)
    assert "\"columns\"" not in example_line
    assert "aggregate: {\"action\":\"aggregate\"" in DATA_TOOL_SYSTEM_PROMPT
    assert result["evidence"]["observations"][0]["result"]["groups"][0]["mean_value"] == "3"
    counters = result["workflow_diagnostics"]["workflow_counters"]
    assert counters["aggregate_columns_validation_feedback"] == 1
    assert counters["guard_activations"][0]["guard"] == "aggregate_columns_validation_feedback"


def test_repeated_identical_aggregate_validation_stops_and_logs_guard_activation(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "records.csv").write_text("value\n2\n", encoding="utf-8")
    repeated = json.dumps({
        "action": "aggregate", "file": "records.csv", "table": "table_0",
        "columns": ["value"], "measures": [{"function": "sum", "column": "value"}],
    }, separators=(",", ":"))
    client = FakeDataToolClient([json.dumps({"action": "profile", "file": "records.csv"}), repeated, repeated, repeated])

    with toolbox_for(root, ["records.csv"]) as toolbox:
        with pytest.raises(DataToolWorkflowError, match="repeated an identical invalid structured data request") as raised:
            gather_dataset_evidence(client, "Sum the values.", toolbox)

    assert len(client.calls) == 3
    counters = raised.value.workflow_diagnostics["workflow_counters"]
    assert counters["aggregate_columns_validation_feedback"] == 2
    assert counters["repeated_invalid_request_stops"] == 1
    assert counters["guard_activations"][-1]["guard"] == "repeated_identical_invalid_request"


def test_generic_header_detection_and_delimited_numeric_normalization(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "contributor-report.csv").write_text(
        "Data Contributors,,,\n\nFTC,,,\n Year , Contributor , # Reports , Rate \n"
        "2024,Office A,1,55%\nYear,Contributor,# Reports,Rate\n"
        "Section,Contributor,# Reports,Rate\n2025,Office B,2,45%\n\nSummary,,3\n",
        encoding="utf-8",
    )
    (root / "payments.csv").write_text(
        "Payment Summary,,\n\nPayment Method,# Reports, Total Loss \n"
        'Card,1,"$1,234"\nTransfer,2,"($234)"\nOther,3,$275M\n',
        encoding="utf-8",
    )

    with toolbox_for(root, ["contributor-report.csv", "payments.csv"]) as toolbox:
        files = {item["path"]: item for item in toolbox.catalog()["files"]}
        report = files["contributor-report.csv"]
        assert report["tables"][0]["columns"] == ["Year", "Contributor", "# Reports", "Rate"]
        assert report["tables"][0]["row_count"] == 2
        assert any("dominant data width 4" in note and "pre-header" in note for note in report["notes"])
        assert any(
            "repeated header-like row(s)" in note and "physical line(s) 6, 7" in note
            for note in report["notes"]
        )
        assert any(
            "non-table record(s)" in note and "physical line(s) 10" in note
            for note in report["notes"]
        )
        report_sum = toolbox.execute({
            "action": "aggregate", "file": "contributor-report.csv", "table": "table_0",
            "measures": [{"function": "sum", "column": "# Reports"}, {"function": "sum", "column": "Rate"}],
        })
        assert report_sum["groups"][0]["sum_# Reports"] == "3"
        assert report_sum["groups"][0]["sum_Rate"] == "100"
        assert any("percentage points" in note for note in report["notes"])

        payment = files["payments.csv"]
        assert payment["tables"][0]["columns"] == ["Payment Method", "# Reports", "Total Loss"]
        payment_sum = toolbox.execute({
            "action": "aggregate", "file": "payments.csv",
            "measures": [{"function": "sum", "column": "Total Loss"}],
        })
        assert payment_sum["groups"][0]["sum_Total Loss"] == "275001000"
        notes = " ".join(payment["notes"])
        assert "currency symbol" in notes
        assert "thousands-separator commas" in notes
        assert "parenthesized value as negative" in notes
        assert "M magnitude suffix" in notes


def test_ambiguous_numeric_encoding_is_reported_and_fails_aggregate_clearly(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "ambiguous.csv").write_text('kind,amount\na,"1,23"\n', encoding="utf-8")
    with toolbox_for(root, ["ambiguous.csv"]) as toolbox:
        notes = toolbox.catalog()["files"][0]["notes"]
        assert any("ambiguous numeric-looking value" in note for note in notes)
        with pytest.raises(DataAccessError, match="ambiguous value.*comma/decimal separator"):
            toolbox.execute({
                "action": "aggregate", "file": "ambiguous.csv",
                "measures": [{"function": "sum", "column": "amount"}],
            })


def test_data_prompt_contains_a_valid_shape_for_every_action():
    for action in ("catalog", "profile", "preview", "filter", "aggregate", "read_text", "search_text", "json_get", "done"):
        assert f'- {action}: {{"action":"{action}"' in DATA_TOOL_SYSTEM_PROMPT


def test_done_requires_coverage_of_all_selected_files_then_accepts_complete_coverage(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    for name, value in (("a.csv", 1), ("b.csv", 2), ("c.csv", 3)):
        (root / name).write_text(f"value\n{value}\n", encoding="utf-8")
    client = FakeDataToolClient([
        json.dumps({"action": "profile", "file": "a.csv"}),
        json.dumps({"action": "done", "summary": "All selected inputs were represented."}),
        json.dumps({"action": "profile", "file": "b.csv"}),
        json.dumps({"action": "profile", "file": "c.csv"}),
        json.dumps({"action": "done", "summary": "Each selected input was profiled."}),
    ])

    with toolbox_for(root, ["a.csv", "b.csv", "c.csv"]) as toolbox:
        result = gather_dataset_evidence(client, "Compare the selected records.", toolbox, max_calls=3)

        assert "task-selected file not yet inspected: b.csv, c.csv" in client.calls[2]["system"]
    assert result["workflow_diagnostics"]["uninspected_task_files"] == []
    counters = result["workflow_diagnostics"]["workflow_counters"]
    assert counters["multi_file_coverage_rejections"] == 1
    assert counters["guard_activations"][0]["guard"] == "task_file_coverage"
    assert counters["guard_activations"][0]["uninspected_files"] == ["b.csv", "c.csv"]
    assert result["workflow_diagnostics"]["tool_invocations"] == 3


def test_tool_rejects_unallowlisted_files_unknown_columns_and_arbitrary_code(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "allowed.csv").write_text("value\n7\n", encoding="utf-8")
    (root / "private.csv").write_text("secret\nno\n", encoding="utf-8")
    with toolbox_for(root, ["allowed.csv"]) as toolbox:
        with pytest.raises(DataAccessError, match="allowlist"):
            toolbox.execute({"action": "preview", "file": "private.csv"})
        with pytest.raises(DataAccessError, match="unknown column"):
            toolbox.execute({"action": "preview", "file": "allowed.csv", "columns": ["private"]})
        with pytest.raises(Exception):
            toolbox.execute({"action": "preview", "file": "allowed.csv", "python": "open('/etc/passwd')"})
