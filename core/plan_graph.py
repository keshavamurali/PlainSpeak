"""Plan graph - DAG for tracking execution steps, replanning, and dependencies."""

import networkx as nx
from datetime import datetime
from typing import Any


class PlanGraph:
    """
    Directed acyclic graph for execution plans, backed by NetworkX DiGraph.
    Nodes represent steps; edges define dependencies.
    """

    ROOT = "ROOT"

    def __init__(self, nodes: list[dict] | None = None, edges: list[dict] | None = None):
        self._g = nx.DiGraph()

        self._add_node(
            self.ROOT,
            description="Initial query",
            agent="System",
            status="completed",
        )
        if nodes:
            for n in nodes:
                nid = n.get("id")
                if nid and nid != self.ROOT:
                    self._add_node(nid, **{k: v for k, v in n.items() if k != "id"})
        if edges:
            for e in edges:
                src = e.get("source")
                tgt = e.get("target")
                if src and tgt:
                    self.add_edge(src, tgt)

    def _add_node(self, node_id: str, **attrs) -> None:
        defaults = {
            "status": "pending",
            "output": None,
            "error": None,
            "start_time": None,
            "end_time": None,
            "description": "",
            "agent": "",
            "reads": [],
            "writes": [],
        }
        data = {**defaults, **attrs}
        self._g.add_node(node_id, **data)

    def add_node(self, node_id: str, **attrs) -> None:
        if not self._g.has_node(node_id):
            self._add_node(node_id, **attrs)

    def add_edge(self, source: str, target: str) -> None:
        self._g.add_edge(source, target)

    def get_predecessors(self, node_id: str) -> list[str]:
        return list(self._g.predecessors(node_id))

    def get_successors(self, node_id: str) -> list[str]:
        return list(self._g.successors(node_id))

    def get_ready_steps(self) -> list[str]:
        """Nodes whose dependencies are complete and status is pending."""
        ready = []
        for nid in self._g.nodes():
            if nid == self.ROOT:
                continue
            status = self._g.nodes[nid].get("status", "pending")
            if status != "pending":
                continue
            preds = self.get_predecessors(nid)
            if all(
                self._g.nodes.get(p, {}).get("status") == "completed"
                for p in preds
            ):
                ready.append(nid)
        return ready

    def all_done(self) -> bool:
        """True if all non-ROOT nodes are completed or failed."""
        for nid in self._g.nodes():
            if nid == self.ROOT:
                continue
            s = self._g.nodes[nid].get("status", "pending")
            if s not in ("completed", "failed"):
                return False
        return True

    def has_pending(self) -> bool:
        """True if any node is still pending or running."""
        return not self.all_done()

    def mark_running(self, node_id: str) -> None:
        self._g.nodes[node_id]["status"] = "running"
        self._g.nodes[node_id]["start_time"] = datetime.utcnow().isoformat()

    def mark_done(self, node_id: str, output: Any = None) -> None:
        data = self._g.nodes[node_id]
        data["status"] = "completed"
        data["end_time"] = datetime.utcnow().isoformat()
        if output is not None:
            data["output"] = output
        if data.get("start_time"):
            try:
                start = datetime.fromisoformat(data["start_time"])
                end = datetime.fromisoformat(data["end_time"])
                data["execution_time"] = (end - start).total_seconds()
            except Exception:
                pass

    def mark_failed(self, node_id: str, error: str | None = None) -> None:
        data = self._g.nodes[node_id]
        data["status"] = "failed"
        data["end_time"] = datetime.utcnow().isoformat()
        data["error"] = error
        if data.get("start_time"):
            try:
                start = datetime.fromisoformat(data["start_time"])
                end = datetime.fromisoformat(data["end_time"])
                data["execution_time"] = (end - start).total_seconds()
            except Exception:
                pass

    def get_node(self, node_id: str) -> dict | None:
        if not self._g.has_node(node_id):
            return None
        return self._g.nodes[node_id]

    def nodes(self):
        """Yield (node_id, data) for each node. Compatible with nodes(data=True) style."""
        return self._g.nodes(data=True)

    def edges(self):
        """Yield (source, target) for each edge."""
        return self._g.edges()

    def to_dict(self) -> dict:
        return {
            "nodes": [
                {"id": nid, **data}
                for nid, data in self._g.nodes(data=True)
            ],
            "edges": [
                {"source": u, "target": v}
                for u, v in self._g.edges()
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlanGraph":
        return cls(
            nodes=data.get("nodes", []),
            edges=data.get("edges", []),
        )
