"""Manual scheduler check: run python -m tests.lopez_testing from the repo root."""

from blackboard.scheduler import Scheduler
from blackboard.core import Blackboard
from blackboard.models import AgentRecord, BoardEntry, PXPTag

board = Blackboard(task_id="consensus-test")

scheduler = Scheduler(board)

a = AgentRecord(agent_id="a", persona="proposer")
b = AgentRecord(agent_id="b", persona="verifier")

board.register_agent(a)
board.register_agent(b)

scheduler.register_agent(a)
scheduler.register_agent(b)

entry1 = BoardEntry(
    agent_id="a",
    tag=PXPTag.REVISE,
    prediction="42",
    explanation="Initial proposal."
)

entry2 = BoardEntry(
    agent_id="b",
    tag=PXPTag.RATIFY,
    prediction="42",
    explanation="The proposal is consistent."
)

entry3 = BoardEntry(
    agent_id="a",
    tag=PXPTag.RATIFY,
    prediction="42",
    explanation="I agree with the verification."
)

print(scheduler.next_agent().agent_id)

scheduler.submit_entry(entry1)
scheduler.submit_entry(entry2)
scheduler.submit_entry(entry3)

print(scheduler.status)

try:
    print(scheduler.next_agent().agent_id)
except RuntimeError as e:
    print(e)
