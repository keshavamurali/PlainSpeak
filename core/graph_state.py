"""Graph-level execution state for HLIG/DTG and plan execution.

This module tracks per-node execution status, inputs, outputs, and errors in a
structured way so that runs can be inspected and visualized after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict


@dataclass
class NodeExecutionRecord:
    """Execution record for a single graph node (plan, HLIG, or DTG)."""

    node_id: str
    agent_name: str
    status: str = "pending"  # pending | running | completed | failed
    started_at: datetime | None = None
    ended_at: datetime | None = None
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    retries: int = 0


class GraphExecutionState:
    """Central graph execution state keyed by node_id.

    This is intentionally generic: node_id may refer to a plan step (T000),
    an HLIG node (HLIG-X), or a DTG node (DTG-X-Y). The runner and generators
    decide which ids to record.
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, NodeExecutionRecord] = {}

    def record_start(self, node_id: str, agent_name: str, inputs: Dict[str, Any] | None = None) -> None:
        rec = self._nodes.get(node_id)
        if rec is None:
            rec = NodeExecutionRecord(node_id=node_id, agent_name=agent_name)
            self._nodes[node_id] = rec
        rec.status = "running"
        rec.started_at = rec.started_at or datetime.utcnow()
        if inputs:
            rec.inputs = dict(inputs)

    def record_end(
        self,
        node_id: str,
        outputs: Dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        rec = self._nodes.get(node_id)
        if rec is None:
            # If we see an end without a start, create a minimal record.
            rec = NodeExecutionRecord(node_id=node_id, agent_name="unknown")
            self._nodes[node_id] = rec
        rec.ended_at = datetime.utcnow()
        if outputs is not None:
            rec.outputs = dict(outputs)
        if error:
            rec.error = error
            rec.status = "failed"
        else:
            rec.status = "completed"

    def to_dict(self) -> Dict[str, Any]:
        return {
            nid: {
                "agent": r.agent_name,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "inputs": r.inputs,
                "outputs": r.outputs,
                "error": r.error,
                "retries": r.retries,
            }
            for nid, r in self._nodes.items()
        }

