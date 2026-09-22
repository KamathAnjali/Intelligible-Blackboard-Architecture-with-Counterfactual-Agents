from blackboard.core import Blackboard
from blackboard.models import (
    AgentRecord,
    BoardEntry,
    IntelligibilityLevel,
)

class Scheduler:
    def __init__(self, board: Blackboard):
        self.board = board
        self.agents : list[AgentRecord] = []
        self.current_index = 0
        self.status = "RUNNING"

    def register_agent(self, agent: AgentRecord) -> None:
        self.agents.append(agent)

    def next_agent(self) -> AgentRecord:
        if not self.agents:
            raise ValueError("No agents registered")

        if not self.is_running():
            raise RuntimeError(f"Scheduler is {self.status}")

        agent = self.agents[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.agents) # Round Robin Scheduling

        return agent

    def submit_entry(self, entry: BoardEntry):
        result = self.board.post_entry(entry)

        if result is not None:
            self.status = "DEADLOCKED"

        elif self.board.get_state().intelligibility in (
            IntelligibilityLevel.STRONG,
            IntelligibilityLevel.ULTRA_STRONG,
        ):
            self.status = "STOPPED"

        return result

    def is_running(self) -> bool:
        return self.status == "RUNNING"