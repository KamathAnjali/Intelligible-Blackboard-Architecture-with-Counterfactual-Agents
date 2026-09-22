# Project Progress & Integration Report

This document outlines the current progress of each team member's branch, when they need to stop working independently, and when they must merge into `main` to unblock the rest of the team.

---

## 1. Student 1 (Data Model & Storage)
* **Branch:** `main` (and `1-Anjali`)
* **Progress:** 
  * Implemented the core `Blackboard` state and thread-safe logic.
  * Implemented JSON snapshot persistence (`store.py`).
  * Implemented PXP validation and deadlock detection logic.
  * Test coverage is 100% for these components.
* **When to merge:** You are already on `main` (the base branch). Your components form the foundation of the project.
* **How long to work independently:** You can continue refining rollback state schemas (for Week 3 counterfactuals) independently, but your Week 1 milestone is done. You are mostly waiting on others to integrate with your code.

---

## 2. Student 2 (Protocol & Scheduler)
* **Branch:** `2-Lopez`
* **Progress:** 
  * Implemented a basic `Scheduler` class with round-robin turns and terminal state (Deadlock/Stopped) handling.
* **When to merge:** **IMMEDIATELY.**
  * The entire project is currently blocked waiting for the Scheduler. Agents cannot take turns and the UI cannot visualize sequence until the Scheduler is merged into `main`.
  * *Note before merging:* Fix the minor architectural inconsistencies first (e.g., duplicate agent registration in the scheduler vs the board, and enforcing that `submit_entry` matches the expected agent's turn). 
* **How long to work independently:** **STOP working independently.** You have reached the minimum viable product for the Week 1 milestone. Open a Pull Request to `main` now.

---

## 3. Student 3 (Agents & Counterfactual)
* **Branch:** `3-Dhruva`
* **Progress:** 
  * Implemented a local Ollama LLM inference script (`llm_client.py`).
  * Defined PEX prompts and a test suite of sample tasks.
* **When to merge:** As soon as `2-Lopez` merges the Scheduler into `main`.
  * Currently, your LLM client is a standalone script that prints to the console. To complete Milestone 1, you must wrap this script in an `Agent` class that accepts a turn from the `Scheduler` and posts a `BoardEntry` to the `Blackboard`. 
* **How long to work independently:** You can continue working independently *only* on refining your prompt engineering and LLM output parsing. However, you cannot integrate your agents into the actual project until the Scheduler is on `main`.

---

## 4. Student 4 (UI & Benchmarking)
* **Branch:** *None yet*
* **Progress:** No code has been pushed.
* **When to merge:** N/A.
* **How long to work independently:** You must immediately branch off `main` and begin building a mock WebSocket server that reads the `BlackboardState` and streams it to a basic frontend graph UI. This can be developed in parallel to Student 2 and 3.
