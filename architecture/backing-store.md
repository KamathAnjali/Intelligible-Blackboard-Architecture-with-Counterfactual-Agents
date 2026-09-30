# ADR: Backing Store Architecture (In-Memory Dict + JSON Snapshots)

**Status**: Accepted  
**Date**: 2026-09-22  
**Author**: Student 1 (Data Model & Storage)

## Context
The Intelligible Blackboard Architecture requires a reliable, thread-safe storage engine to maintain the session state (registered agents, chronological PXP message ledger, intelligibility classification, deadlock occurrences, and counterfactual simulation branches). 

We evaluated two primary options:
1. **In-Memory Python Dict + Local JSON Snapshots**
2. **Distributed Store (Redis / Key-Value Database)**

## Decision
We adopt an **in-memory dictionary backed by local JSON snapshots** (`InMemoryJSONStore`), deliberately deferring Redis to a future milestone if multi-process distribution becomes necessary.

## Rationale & Key Reasons

1. **Zero External Dependencies & Rapid Local Prototyping**:
   By using native Python dictionaries synchronized via `threading.RLock`, teammates can clone the repo and run agents immediately without provisioning or configuring Redis services or docker containers.

2. **Ultra-Low Latency & Thread Safety for Local Execution**:
   All 3-4 LLM agents execute within the same runtime environment. In-memory dictionary lookups and mutations execute in microseconds under lock protection, with zero network serialization bottlenecks.

3. **Human-Readable Auditing & Native Snapshot Replay**:
   Pydantic JSON serialization (`model_dump_json()`) produces clean, human-readable snapshot files. These files directly fulfill the requirements for offline debugging, history inspection, and counterfactual rollback sandbox initialization without needing database query tooling.

## Interface Contract
The store implements the abstract `BackingStore` base class:
- `load(task_id: str) -> BlackboardState | None`
- `save(state: BlackboardState) -> None`
- `snapshot_to_disk(task_id: str, tag: str | None = None) -> Path`
- `load_snapshot_from_disk(task_id: str, filepath: str | Path | None = None) -> BlackboardState`
- `list_snapshots(task_id: str | None = None) -> list[Path]`
- `export_replay_trace(task_id: str) -> list[dict]`
