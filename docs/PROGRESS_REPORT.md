# Progress report

Updated 29 September 2026 for `3-Dhruva`. This branch contains four personas, an explanation self-check, and a runnable two-agent local inference path. The counterfactual mechanism and full product integration are still pending.

## Implemented

| Area | Current behavior |
| --- | --- |
| Shared contract | Pydantic models define agents, PXP-tagged BoardEntry records, target references, UTC timestamps, deadlocks, and session state. Empty predictions and explanations are rejected. |
| Blackboard and storage | Registered agents can post entries to a lock-protected, chronological board. The board validates agent and target IDs. An in-memory store supports JSON snapshots. |
| Scheduler | Round-robin turns select an agent and stop after the board reports agreement or four consecutive `REFUTE`/`REJECT` tags. The conversation runner also has a configurable turn limit. |
| Local inference | Ollama uses `qwen3:4b-instruct-2507-q4_K_M` with context 4096, temperature 0, seed 42, and a 256-token output cap. Raw responses and timing are available. |
| Structured output | PEX requires `prediction` and `explanation`; PXP adds a tag. Strict validation rejects malformed, missing, extra, or blank fields. Up to five retries can be configured, with two by default. The same budget covers rejected explanation reviews. |
| Personas and mapping | Aggressive proposer, cautious verifier, evidence auditor, and counterexample challenger have distinct prompts and share the PEX/PXP contracts. Application code supplies agent ID, target ID, entry ID, timestamp, and simulation flag when it builds a BoardEntry. |
| Explanation self-check | A separate call reviews the prediction and explanation against the original task. Unsupported explanations or malformed reviews trigger bounded regeneration before an entry is created. Every review, failure, and generation attempt is retained; usage includes both generation and review calls. |
| Live sessions | The runner registers both agents, gives each the original task and latest board history, posts validated entries through the scheduler, and saves a transcript and board snapshot. Errors preserve the partial run. |
| Demo | `make demo` checks a live three-turn `REVISE`, `RATIFY`, `RATIFY` sequence, matching predictions, one valid attempt per turn, and no recorded failures. |

The storage design uses an append-only entry list for chronological replay and a simple in-memory JSON store for short local sessions. No database service is required.

## Evidence and prompt revision

- Five initial PEX sample tasks returned the expected predictions: arithmetic, deduction, insufficient evidence, contradiction, and a constrained selection. This was a smoke check, not a benchmark.
- All four personas passed 12 live checks covering deduction, insufficient evidence, and contradiction. Every candidate and explanation review passed on its first attempt, with the expected prediction. The prompts distinguish existential from universal claims and reserve RATIFY for supported predictions and reasoning.
- The self-check rejected both an unsupported universal claim and an irrelevant explanation attached to a correct prediction. Controlled checks verified correction feedback, bounded rejection, malformed-review handling, and preservation of the board and failure records.
- On 29 September 2026, the new challenger generated a self-checked BoardEntry through WSL, and `make demo RETRIES=0` completed `REVISE`, `RATIFY`, `RATIFY` with `yes`. The demo used six model calls: one generation and one review per turn. Its local record is `results/conversations/conversation-20260929T075505206911Z/transcript.json`.
- On the RX 6800S, a prior Windows Vulkan check reported the model fully on GPU and about 54 output tokens per second on a fixed prompt; the earlier CPU baseline was about 16. Placement and latency vary by machine and session.

Generated transcripts and inference reports are excluded from Git. They contain raw responses, explanation reviews, attempts, total call/token usage, timing, prompt settings, entries, and the final board state.

## Current limits

- The board's agreement status is based on recent RATIFY tags. It does not independently prove that distinct agents agree on the same reasoning or that a prediction is correct. The runner flags different RATIFY prediction text for review. The self-check can repeat the same model's mistakes; independent correctness and semantic evaluation remain required.
- The scheduler does not enforce that `submit_entry()` receives the agent selected by `next_agent()`. The current runner follows that order, but the shared interface needs turn ownership checks before broader integration.
- The current branch has no connected UI, benchmark execution pipeline, or counterfactual replay. Counterfactual fields in the data model are placeholders for planned behavior.
- The two-agent demonstration covers a simple deductive task. It does not establish recovery from disagreement, benchmark accuracy, or the proposed counterfactual benefit.

The next integration steps are to connect the real board history to the UI, define enforceable turn and consensus rules, add a third agent, and implement isolated counterfactual replay before running the planned evaluations. The [milestones](PROJECT_MILESTONES.md) describe those checkpoints.
