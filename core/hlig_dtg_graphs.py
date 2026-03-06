"""
HLIG and DTG graphs using NetworkX. Serialization to JSON for display/logging.

CVP (Causal Visual Programming) extensions:
- Causal edge semantics: edges may have causal=true to denote direct causation
- Causal path traceability: get_causal_path() returns ancestor chain for audit
- Markov blanket scoping: get_causal_parents() restricts agent context to causal parents
"""

import json
from typing import Any

import networkx as nx

try:
    from networkx.exception import NetworkXUnfeasible
except ImportError:
    NetworkXUnfeasible = type("NetworkXUnfeasible", (Exception,), {})


class GraphCycleError(Exception):
    """Raised when an HLIG or DTG graph contains a cycle. Used to retrigger planner/designer."""

    def __init__(self, graph_type: str, cycle_edges: list, message: str = "", hlig_node_id: str | None = None):
        self.graph_type = graph_type
        self.cycle_edges = cycle_edges
        self.hlig_node_id = hlig_node_id
        msg = message or (
            f"{graph_type} graph contains a cycle. "
            f"Cycle edges: {cycle_edges}. "
            "Dependencies must form a DAG (no cycles)."
        )
        super().__init__(msg)


def _raise_if_cycle(
    g: nx.DiGraph,
    graph_label: str,
    hlig_node_id: str | None = None,
) -> None:
    """Raise GraphCycleError if the directed graph contains a cycle."""
    if not nx.is_directed_acyclic_graph(g):
        try:
            cycle_edges = list(nx.find_cycle(g))
            # Prefer edge list as (u, v) for clarity
            cycle_repr = [f"{u} -> {v}" for u, v in cycle_edges]
        except Exception:
            cycle_repr = ["(cycle detected but could not enumerate)"]
        raise GraphCycleError(
            graph_label,
            cycle_repr,
            hlig_node_id=hlig_node_id,
        )


def _safe_serialize(obj: Any) -> Any:
    """Convert object for JSON (handle non-JSON types)."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v) for k, v in obj.items()}
    return str(obj)


class DTGGraph:
    """Detailed Task Graph - NetworkX DiGraph for one HLIG node's sub-tasks."""

    def __init__(self, hlig_node_id: str, nodes: list[dict] | None = None, edges: list[dict] | None = None):
        self.hlig_node_id = hlig_node_id
        self._g = nx.DiGraph()
        if nodes:
            for n in nodes:
                nid = n.get("id")
                if nid:
                    attrs = {k: v for k, v in n.items() if k != "id"}
                    self._g.add_node(nid, **attrs)
        if edges:
            for e in edges:
                src = e.get("from") or e.get("source")
                tgt = e.get("to") or e.get("target")
                if src and tgt:
                    attrs = {k: v for k, v in e.items() if k not in ("from", "to", "source", "target")}
                    self._g.add_edge(src, tgt, **attrs)

    def add_node(self, node_id: str, **attrs) -> None:
        self._g.add_node(node_id, **attrs)

    def add_edge(self, source: str, target: str, **attrs) -> None:
        self._g.add_edge(source, target, **attrs)

    def to_dict(self, hlig_node: dict | None = None) -> dict:
        """
        JSON-serializable dict for display/logging.
        If hlig_node is provided and nodes lack parent_hlig, enriches each node with
        parent_hlig and language for self-contained agent execution (backward compat).
        """
        nodes = [{"id": nid, **_safe_serialize(dict(data))} for nid, data in self._g.nodes(data=True)]
        if hlig_node and isinstance(hlig_node, dict):
            parent_hlig = {
                "id": hlig_node.get("id", ""),
                "task": hlig_node.get("task"),
                "inputs": hlig_node.get("inputs", []),
                "outputs": hlig_node.get("outputs", []),
                "language": hlig_node.get("language", "Rust, Tauri, React, CSS"),
                "external_interfaces": hlig_node.get("external_interfaces", []),
            }
            lang = hlig_node.get("language", "Rust, Tauri, React, CSS")
            for n in nodes:
                if isinstance(n, dict) and "parent_hlig" not in n:
                    n["parent_hlig"] = parent_hlig
                    n["language"] = lang
        edges = []
        for u, v, data in self._g.edges(data=True):
            e = {"from": u, "to": v, **_safe_serialize(dict(data))}
            edges.append(e)
        return {
            "hlig_node_id": self.hlig_node_id,
            "nodes": nodes,
            "edges": edges,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: dict) -> "DTGGraph":
        hlig_id = data.get("hlig_node_id", "")
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        inst = cls(hlig_id, nodes=nodes, edges=edges)
        _raise_if_cycle(inst._g, "DTG", hlig_node_id=hlig_id)
        return inst


class HLIGGraph:
    """High-Level Intent Graph - NetworkX DiGraph with optional DTG per node."""

    def __init__(self, nodes: list[dict] | None = None, edges: list[dict] | None = None):
        self._g = nx.DiGraph()
        if nodes:
            for n in nodes:
                nid = n.get("id")
                if nid:
                    attrs = {k: v for k, v in n.items() if k != "id" and k != "dtg"}
                    self._g.add_node(nid, **attrs)
        if edges:
            for e in edges:
                src = e.get("from") or e.get("source")
                tgt = e.get("to") or e.get("target")
                if src and tgt:
                    attrs = {k: v for k, v in e.items() if k not in ("from", "to", "source", "target")}
                    self._g.add_edge(src, tgt, **attrs)

    def get_causal_parents(self, node_id: str) -> list[str]:
        """
        CVP: Return causal parents of node_id (nodes with edges TO this node).
        Only includes edges where causal=true. If no causal edges exist, falls back to all
        incoming edges (backward compat for graphs without causal metadata).
        """
        if not self._g.has_node(node_id):
            return []
        parents = []
        for pred in self._g.predecessors(node_id):
            edge_data = self._g.edges.get((pred, node_id), {})
            is_causal = edge_data.get("causal", True)
            if is_causal:
                parents.append(pred)
        return parents

    def get_causal_path(self, node_id: str) -> list[tuple[str, dict]]:
        """
        CVP: Return causal path - ordered list of (ancestor_id, node_data) from roots to node_id.
        Used for traceability: which nodes led to this one (Markov blanket ancestors).
        """
        if not self._g.has_node(node_id):
            return []
        ancestors = set()
        to_visit = list(self.get_causal_parents(node_id))
        while to_visit:
            nid = to_visit.pop()
            if nid in ancestors:
                continue
            ancestors.add(nid)
            to_visit.extend(self.get_causal_parents(nid))
        # Topological order: sort ancestors so dependencies come first
        try:
            order = list(nx.topological_sort(self._g))
            ordered = [(nid, dict(self._g.nodes[nid])) for nid in order if nid in ancestors]
        except (nx.NetworkXError, NetworkXUnfeasible):
            ordered = [(nid, dict(self._g.nodes[nid])) for nid in ancestors]
        return ordered

    def topological_order(self) -> list[str]:
        """Return HLIG node IDs in topological order (parents before children)."""
        try:
            return list(nx.topological_sort(self._g))
        except (nx.NetworkXError, NetworkXUnfeasible):
            return list(self._g.nodes())

    def set_node_dtg(self, node_id: str, dtg: DTGGraph) -> None:
        """Attach a DTG graph to an HLIG node."""
        if self._g.has_node(node_id):
            self._g.nodes[node_id]["dtg"] = dtg

    def get_node_dtg(self, node_id: str) -> DTGGraph | None:
        """Get DTG for an HLIG node."""
        if not self._g.has_node(node_id):
            return None
        return self._g.nodes[node_id].get("dtg")

    def nodes(self):
        return self._g.nodes(data=True)

    def edges(self):
        return self._g.edges(data=True)

    def to_dict(self) -> dict:
        """JSON-serializable dict for display/logging. DTGs included as dicts."""
        nodes = []
        for nid, data in self._g.nodes(data=True):
            node = {"id": nid, **_safe_serialize({k: v for k, v in data.items() if k != "dtg"})}
            dtg = data.get("dtg")
            hlig_node = {"id": nid, **{k: v for k, v in data.items() if k != "dtg" and k != "dtg_root"}}
            if isinstance(dtg, DTGGraph):
                node["dtg"] = dtg.to_dict(hlig_node=hlig_node)
            elif isinstance(dtg, dict):
                node["dtg"] = dtg
            nodes.append(node)
        edges = []
        for u, v, data in self._g.edges(data=True):
            e = {"from": u, "to": v, **_safe_serialize(dict(data))}
            edges.append(e)
        return {"nodes": nodes, "edges": edges}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_planner_hlig(cls, planner_output: dict) -> "HLIGGraph | None":
        """Build HLIGGraph from planner artifact (hlig.nodes, hlig.edges)."""
        hlig = planner_output.get("hlig") if isinstance(planner_output, dict) else None
        if not hlig or not isinstance(hlig, dict):
            return None
        nodes = hlig.get("nodes", [])
        edges_raw = hlig.get("edges", [])
        if not nodes:
            return None
        # Parse edges with CVP causal semantics and interface contracts
        edges = []
        for e in edges_raw:
            if not e.get("from") or not e.get("to"):
                continue
            edge_attrs = {
                "interface_type": e.get("interface_type", "dependency"),
                # CVP: causal defaults to True when omitted (backward compat)
                "causal": e.get("causal", True) if "causal" in e else True,
            }
            if e.get("interface_spec"):
                edge_attrs["interface_spec"] = e["interface_spec"]
            if e.get("interface_ref"):
                edge_attrs["interface_ref"] = e["interface_ref"]
            edges.append({"from": e["from"], "to": e["to"], **edge_attrs})
        inst = cls(nodes=nodes, edges=edges)
        _raise_if_cycle(inst._g, "HLIG")
        return inst
