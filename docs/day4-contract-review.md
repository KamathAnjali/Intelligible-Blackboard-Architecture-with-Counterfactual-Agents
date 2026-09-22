# Day 4: Student 3 review of the shared PXP contract

Reviewed on 2026-09-22 against the Excel's Week 1 Plan, Day 4, Student 3.
Scope: review compatibility and record comments before the Day 5 adapter.

## Versions reviewed

| Branch | Commit at review |
| --- | --- |
| `3-Dhruva` before Day 4 | `6196902911cd152600f4b0b2c41af20e619dd424` |
| `origin/main` | `2bc84c62fd05dca4d5b5d37b03f6f8a1ab3bc34b` |
| `origin/1-Anjali` | `2bc84c62fd05dca4d5b5d37b03f6f8a1ab3bc34b` |
| `origin/2-Lopez` | `355064b1b274ab2d2e8355eb28142cbdd6d631c0` |

`blackboard/models.py` is byte-identical across all four refs; its Git blob is
`9bbfde9dee81e781f6ef9615331c52e0757b7fa2`. Therefore the local compatibility
tests exercise the same BoardEntry and PXPTag definitions as those remote
branches. Also reviewed `docs/Schema_Draft.md` and `test_parser.py` on main,
and the board's agent/target checks in `blackboard/core.py`.

## Field mapping review

| BoardEntry field | Source in the planned Day 5 adapter | Compatibility |
| --- | --- | --- |
| `prediction` | Validated PEX response | Already a nonempty string; direct copy. |
| `explanation` | Validated PEX response | Already a nonempty string; direct copy. |
| `tag` | Agent's protocol decision, validated against PXPTag | Not currently part of PEX; must be added deliberately in Day 5. |
| `agent_id` | Registered agent's identity in application code | Required; should not be invented by the model. |
| `target_entry_id` | Entry selected from the supplied board context | Optional in schema; validate any non-null ID against board history. |
| `entry_id` | BoardEntry default UUID factory | Leave to application/schema. |
| `timestamp` | BoardEntry default UTC clock | Leave to application/schema. |
| `is_counterfactual_sim` | Application execution context | Defaults to false; sandbox code owns this later. |

Conclusion: both PEX fields fit the shared contract without changing their
names or types. A PEX object alone is not a BoardEntry: `agent_id` and `tag`
are additionally required. The application still needs to register the agent
and resolve the response target before submitting to the board.

`test_agent_contract.py` constructs synthetic entries for each of the four tags,
checks JSON round trips and default metadata, and verifies that bare PEX is
rejected as an incomplete BoardEntry. This is a compatibility test, not a
production adapter or a live agent-to-board interaction.

## Tag review

| Tag | Meaning in the current shared enum |
| --- | --- |
| `RATIFY` | Aligns with both prediction and explanation. |
| `REVISE` | Adjusts the agent's own view and writes an updated thesis. |
| `REFUTE` | Rejects the position and cannot self-correct; records a roadblock. |
| `REJECT` | Conflicts with both prediction and explanation. |

Agent prompts should use these definitions consistently when Day 5 introduces
tag selection. The parser validates the enum value, not whether the underlying
reasoning justifies the tag. The current PEX schema deliberately rejects a
`tag` field; extend the output contract explicitly rather than silently dropping
or defaulting a model-produced tag.

## Review comments for integration

1. **Schema freeze status:** the interfaces match, but the available document is
   named `Schema_Draft.md` and no explicit team freeze approval was found in the
   reviewed files. This records Student 3's review of the current version; it
   does not claim that Students 1 and 2 have approved a freeze. Recheck the
   schema if their branches change before integration.
2. **First contribution:** there is no INIT tag. The schema draft uses REVISE
   with a null target for an initial proposal. Confirm that convention with
   the protocol owner before hardcoding it in the Day 5 adapter.
3. **Validation boundary:** BoardEntry accepts extra fields by Pydantic's default
   ignore behavior, while PEX forbids them. Keep strict model-output validation
   and construct metadata explicitly. Do not pass an arbitrary model dictionary
   directly into BoardEntry and assume extra fields were rejected.
4. **References:** constructing BoardEntry does not prove that agent/target IDs
   exist. `Blackboard.post_entry` enforces registration and target existence.
   Select IDs from application context and handle posting errors explicitly.
5. **Ownership labels:** the module header still describes three-person roles;
   the Excel assigns Student 1 storage, Student 2 protocol/scheduler, Student 3
   agents/counterfactual, and Student 4 UI/benchmarking. Use the Excel's ownership
   when coordinating changes.

No shared schema change is needed for the two existing PEX fields. The remaining
tag-selection and posting implementation belongs to Day 5. Review comments are
kept in this branch for teammates to inspect; no team approval is implied.
