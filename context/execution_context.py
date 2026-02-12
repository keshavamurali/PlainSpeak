"""Execution context for tracking agent runs and managing shared state."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
import uuid

if TYPE_CHECKING:
    from agents.base import BaseAgent


@dataclass
class RunRecord:
    """Record of a single agent run."""

    agent_name: str
    agent: "BaseAgent"
    started_at: datetime
    ended_at: datetime | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class ExecutionContext:
    """Tracks execution state across agent pipeline runs."""

    def __init__(
        self,
        session_id: str | None = None,
        initial_input: dict[str, Any] | None = None,
    ):
        self.session_id = session_id or str(uuid.uuid4())
        self.created_at = datetime.utcnow()
        self.state: dict[str, Any] = dict(initial_input or {})
        self.artifacts: dict[str, Any] = {}
        self.run_history: list[RunRecord] = []
        self._current_run: RunRecord | None = None
        # globals_schema: key-value store passed between agents (S18-style reads/writes)
        self.globals_schema: dict[str, Any] = {}

    @classmethod
    def create(cls, **kwargs: Any) -> "ExecutionContext":
        """Create a new execution context with optional initial state."""
        return cls(initial_input=kwargs)

    def get_inputs(self, reads: list[str]) -> dict[str, Any]:
        """Get input data for agent from globals_schema (S18-style)."""
        inputs = {}
        for key in reads:
            if key in self.globals_schema:
                inputs[key] = self.globals_schema[key]
            else:
                if key in self.state:
                    inputs[key] = self.state[key]
        return inputs

    def write_output(self, key: str, value: Any) -> None:
        """Write agent output to globals_schema for downstream agents."""
        self.globals_schema[key] = value

    def add_artifact(self, key: str, value: Any) -> None:
        """Add an artifact produced by an agent."""
        self.artifacts[key] = value

    def get_artifact(self, key: str, default: Any = None) -> Any:
        """Retrieve an artifact by key."""
        return self.artifacts.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        """Set state key-value."""
        self.state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get state value by key."""
        return self.state.get(key, default)

    def record_agent_run(self, agent_name: str, agent: "BaseAgent") -> None:
        """Record that an agent has completed a run."""
        if self._current_run and self._current_run.agent_name == agent_name:
            self._current_run.ended_at = datetime.utcnow()
            self._current_run.outputs = dict(self.artifacts)
            self.run_history.append(self._current_run)
            self._current_run = None
        else:
            record = RunRecord(
                agent_name=agent_name,
                agent=agent,
                started_at=datetime.utcnow(),
                ended_at=datetime.utcnow(),
                outputs=dict(self.artifacts),
            )
            self.run_history.append(record)

    def to_dict(self) -> dict[str, Any]:
        """Serialize context for logging/persistence."""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "state": self.state,
            "globals_schema": self.globals_schema,
            "artifacts": self.artifacts,
            "runs": [
                {
                    "agent": r.agent_name,
                    "started": r.started_at.isoformat(),
                    "ended": r.ended_at.isoformat() if r.ended_at else None,
                    "error": r.error,
                }
                for r in self.run_history
            ],
        }
