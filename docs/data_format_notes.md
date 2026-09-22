# KramaBench Dataset Data Format Notes

**Project:** Intelligible Blackboard (Benchmarking Pipeline)  
**Author:** Student 4 (UI & Benchmarking)  
**Date:** September 22, 2026  

---

## 1. Overview & Dataset Structure

KramaBench is a multi-step reasoning benchmark designed to evaluate multi-agent blackboard systems across complex logical deduction, mathematical reasoning, and multi-agent coordination problems.

Datasets are supplied in **JSON** or **JSONL** (JSON Lines) format, where each line represents a single benchmark trial.

---

## 2. Schema Specification

Below is the standard JSON structure of a raw KramaBench record:

```json
{
  "task_id": "kb_math_042",
  "domain": "mathematical_deduction",
  "difficulty": "hard",
  "problem_statement": "In a group of 5 agents, Agent A proposes x = 12. Agent B refutes claiming x^2 = 144 allows x = -12. Find all valid integer solutions under constraints...",
  "expected_answer": "x = 12 or x = -12",
  "ground_truth_reasoning_steps": [
    "Identify core equation x^2 = 144",
    "Evaluate domain constraints",
    "Formulate final solution set {-12, 12}"
  ],
  "metadata": {
    "source_dataset": "kramabench_v1",
    "max_allowed_turns": 15,
    "consensus_threshold": 0.85,
    "tags": ["algebra", "multi-agent-conflict", "counterfactual-test"]
  }
}
```

---

## 3. Field Breakdown & Mapping Requirements

| Raw Field Name | Data Type | Description | Target Internal Field (`TaskFormat`) |
| :--- | :--- | :--- | :--- |
| `task_id` | `str` | Unique benchmark problem identifier | `task_id` |
| `problem_statement` | `str` | Full text prompt presented to the agent group | `task_text` |
| `expected_answer` | `str` | Canonical ground-truth answer for evaluation | `expected_answer` |
| `domain` | `str` | Category (e.g. `logic`, `math`, `planning`) | `domain` |
| `difficulty` | `str` | Difficulty level (`easy`, `medium`, `hard`) | `difficulty` |
| `ground_truth_reasoning_steps` | `list[str]` | Intermediate validation steps (optional) | `reference_steps` |
| `metadata` | `dict` | Execution parameters (`max_allowed_turns`, etc.) | `metadata` |

---

## 4. Considerations for `bench/ingest/krama_parser.py`

1. **Robust Validation:**
   - Missing `task_id` should trigger auto-generation (e.g., `kb_gen_<uuid>`).
   - `problem_statement` / `task_text` is mandatory; raise `ValueError` if empty or missing.
2. **Flexible Ingest Input:**
   - Support parsing from:
     - Direct python dictionaries (`parse_krama_record(dict)`)
     - Raw JSON string (`parse_krama_json(str)`)
     - File paths to `.json` or `.jsonl` datasets (`load_krama_dataset(filepath)`).
3. **Compatibility with Student 1 Board Store:**
   - Internal `TaskFormat` object must produce a clean prompt payload to initialize `BlackboardState(task_id=record.task_id)`.
