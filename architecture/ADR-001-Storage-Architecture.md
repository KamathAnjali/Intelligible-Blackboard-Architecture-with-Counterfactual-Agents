# ADR 001: Blackboard Storage Architecture

**Status**: Accepted  
**Date**: 2026-09-22  

## Context
The intelligible blackboard architecture requires a storage mechanism to hold the evolving state of the session, including agent entries, deadlock counts, and intelligibility scores. This state needs to be accessed by the scheduler, updated by agents concurrently, and inspected by the UI dashboard or rollback mechanisms. We considered two primary approaches:
1. An **in-memory data structure (dict) with JSON snapshots** to disk.
2. A **distributed key-value store like Redis**.

## Decision
We will use an **in-memory dict with JSON snapshots** for the initial implementation, deliberately deferring the use of Redis for now. 

## Rationale

1. **Simplicity and Iteration Speed**: An in-memory Python dictionary combined with standard JSON file saving/loading introduces zero external dependencies or infrastructure overhead. This allows the team to rapidly prototype the schemas and iteration logic during the critical Week 1 and Week 2 milestones without getting bogged down debugging network or serialization issues across a separate external service.
2. **Sufficient for Local Experimental Scope**: The immediate project scope involves running 3-4 local LLM agents in a controlled experimental setup. The volume of data (a few hundred messages per session at most) easily fits into memory. Standard threading locks are sufficient to handle local concurrency from a single scheduler process.
3. **Native Snapshot Traceability**: Serializing the entire state to a human-readable JSON snapshot file at key points natively supports the requirements for saving/loading state, inspecting history, and satisfying the "rollback contract" for counterfactual replays. JSON files natively solve the persistence requirement without needing to query a Redis database.
