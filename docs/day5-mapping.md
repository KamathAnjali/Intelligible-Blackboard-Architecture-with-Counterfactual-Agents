# Day 5: map model responses to BoardEntry

Student 3, 2026-09-22. Assignment: Week 1 Plan, Day 5, Excel cells F10:G10.
The production adapter is `OllamaClient.generate_entry()` in `agents/llm_client.py`.

## Interface

Inputs: original task, a BlackboardState snapshot, the acting agent's registered
ID, optional existing target ID, retry budget, and application-owned simulation
flag. If the target is omitted on a nonempty board, the application selects the
latest entry. The adapter checks registration, active status, persona support,
model agreement, and target existence before inference.

The model receives the task and serialized history under the PXP prompt. It
returns `tag`, `prediction`, and `explanation`, validated using the shared
PXPTag enum and the existing strict PEX field rules. The new schema rejects
extra fields, including model-generated metadata. Invalid output follows the
same bounded retry mechanism as PEX; failure raises PXPGenerationError with
all attempts available for inspection.

The application constructs BoardEntry with:

| Field | Owner |
| --- | --- |
| tag, prediction, explanation | Validated model output |
| agent_id | Registered acting agent |
| target_entry_id | Application-selected existing board entry, or null initially |
| entry_id, timestamp | Shared BoardEntry default factories |
| is_counterfactual_sim | Caller, false by default |

The result includes the actual `entry` object, parsed model fields, final raw
response, and every attempt with token/timing data. The adapter deep-copies its
input state and never posts or mutates board state. The caller can hand the
entry to `Scheduler.submit_entry()` during its turn. Registration in both the
current scheduler and board is still required; the board rechecks references.

## Opening tag and shared contract

The shared schema remains unchanged at blob
`9bbfde9dee81e781f6ef9615331c52e0757b7fa2`, including the merged scheduler's base.
For an empty board, the adapter requires REVISE and a null target, following
the existing `docs/Schema_Draft.md` initial-entry example and `demo.py`.
This is the project's current documented convention, not evidence of a separate
team freeze approval. The opening schema restricts the tag to REVISE; a model
returning RATIFY on an empty board fails validation rather than being silently
rewritten. On nonempty history, all four shared tags are accepted structurally.
Whether a tag is semantically justified still requires reasoning evaluation.

## Verification

- Offline suite after relocation: 61 passed, 8 opt-in live checks skipped.
- Full selected live suite: 8 passed (two Day 5 mapping cases and six Day 4
  persona regressions), with zero retries allowed in the live tests.
- Initial case: REVISE, prediction `yes`, correct tulip-to-plant explanation,
  application agent ID, generated message ID/UTC timestamp, null target.
- Reply case: RATIFY, prediction `yes`, correct explanation, exact existing
  target ID supplied by the application.
- Both live cases preserved their input snapshots.
- Offline checks cover all four reply tags, explicit/default targets, unknown
  or inactive agents, model mismatch, extra metadata, invalid/missing tags,
  retry exhaustion, and a generated entry passing through the real scheduler
  into a temporary board store.

Local raw reports (ignored by Git):
`day5-entry-20260922T131522087711Z.json`,
`day5-entry-20260922T131528840567Z.json`, and
`day4-personas-20260922T131557101831Z.json`, all under `results/ollama/`.
No model change or follow-up prompt tuning was needed after the new PXP
template was introduced.

## Repository organization and limits

All test scripts and pytest hooks are in `tests/`, including the manual
`tests/lopez_testing.py` script. `pytest.ini` controls discovery and the project
import path. Use `make test`, `make personas`, and `make entry` for the main
checks. For the manual scheduler script use `python -m tests.lopez_testing`.

Generated `results/`, store `snapshots/`, virtual environments, Python caches,
coverage output, and local `.env` files are ignored. No generated files are
tracked. Removed the byte-identical root milestone copy left by the merge;
the canonical document is `docs/PROJECT_MILESTONES.md`. The previously stashed
local edit is preserved and is not included in the commit.

The adapter sends the supplied snapshot's full history; it is intended for
short Week 1 sessions. Context-budget selection is not implemented. Day 6
still needs the real two-agent conversation loop, failure log, and checks on
turn order and termination using the merged scheduler.
