"""
HLIG and DTG graphs using NetworkX. Serialization to JSON for display/logging.

CVP (Causal Visual Programming) extensions:
- Causal edge semantics: edges may have causal=true to denote direct causation
- Causal path traceability: get_causal_path() returns ancestor chain for audit
- Markov blanket scoping: get_causal_parents() restricts agent context to causal parents
"""

import json
from pathlib import Path
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


def _split_hlig_control_and_data_edges(edges: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split HLIG edge dicts into control-flow vs data-flow (same rule as HLIGGraph.__init__)."""
    control: list[dict] = []
    data: list[dict] = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        et = (e.get("edge_type") or "").lower()
        if et == "data":
            data.append(e)
        else:
            control.append(e)
    return control, data


def _node_kind(node: dict[str, Any]) -> str:
    """Return normalized node kind across legacy/new schemas."""
    if not isinstance(node, dict):
        return ""
    kind = str(node.get("kind") or "").strip().lower()
    if kind:
        return kind
    node_type = str(node.get("node_type") or "").strip().lower()
    if node_type == "contract":
        return "contract"
    nid = str(node.get("id") or "").strip().upper()
    if "-DTG-" in nid or nid.startswith("DTG-"):
        return "atomic"
    if "-CONTRACT-" in nid or node_type == "contract":
        return "contract"
    return "composite"


def break_hlig_control_edges_until_dag(
    control_edges: list[dict],
    node_ids: set[str],
) -> tuple[list[dict], list[str]]:
    """
    Remove control edges until the graph is a DAG.

    Each iteration finds one directed cycle and removes one edge on that cycle:
    prefer soft/implicit edges first and preserve causal explicit edges where possible.
    Tie-break: lexicographically larger (from, to).

    Preserves full dicts for remaining edges. Returns (new_control_edges, removed_summaries).
    """
    removed_summaries: list[str] = []
    working: list[dict] = [dict(e) for e in control_edges if isinstance(e, dict)]

    def _graph_from(edgelist: list[dict]) -> nx.DiGraph:
        g = nx.DiGraph()
        for nid in node_ids:
            g.add_node(nid)
        for e in edgelist:
            u, v = e.get("from"), e.get("to")
            if u and v:
                if not g.has_node(u):
                    g.add_node(u)
                if not g.has_node(v):
                    g.add_node(v)
                g.add_edge(u, v)
        return g

    max_iter = len(working) + 8  # safety
    iterations = 0
    while iterations < max_iter:
        iterations += 1
        g = _graph_from(working)
        if nx.is_directed_acyclic_graph(g):
            break
        try:
            cycle = nx.find_cycle(g, orientation="original")
        except Exception:
            break
        if not cycle:
            break
        pairs: list[tuple[str, str]] = []
        for item in cycle:
            pairs.append((item[0], item[1]))
        pair_set = set(pairs)

        def _strip_priority(e: dict) -> tuple:
            """Higher = remove first: prefer removing soft/non-causal edges first."""
            u, v = e.get("from") or "", e.get("to") or ""
            dep = str(e.get("dependency_type") or "").lower()
            causal = bool(e.get("causal", True))
            soft_rank = 1 if dep == "soft" else 0
            causal_rank = 0 if causal else 1
            return (soft_rank, causal_rank, u, v)

        match_indices = [
            i for i, e in enumerate(working) if (e.get("from"), e.get("to")) in pair_set
        ]
        if not match_indices:
            break
        rm_i = max(match_indices, key=lambda i: _strip_priority(working[i]))
        e_rm = working.pop(rm_i)
        fu, fv = e_rm.get("from"), e_rm.get("to")
        removed_summaries.append(f"{fu} -> {fv}")

    return working, removed_summaries


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


def planner_hlig_completeness_errors(hlig: dict[str, Any]) -> list[str]:
    """
    Minimum structural bar for a multi-subsystem HLIG (planning phase).

    Rejects under-spec model output such as a lone frontend node with no edges,
    no backend, and no contract nodes—common failure mode when the model over-simplifies.
    """
    errors: list[str] = []
    if not isinstance(hlig, dict):
        return ["HLIG must be a JSON object."]
    is_recursive_shape = isinstance(hlig.get("graph"), dict)
    if is_recursive_shape:
        g = hlig.get("graph") or {}
        nodes = g.get("nodes") or []
        edges = g.get("edges") or []
    elif isinstance(hlig.get("hlig"), dict):
        g = hlig.get("hlig") or {}
        nodes = g.get("nodes") or []
        edges = g.get("edges") or []
    else:
        nodes = hlig.get("nodes") or []
        edges = hlig.get("edges") or []
    def _walk_nodes(ns: list[dict]) -> list[dict]:
        out: list[dict] = []
        for node in ns:
            if not isinstance(node, dict):
                continue
            out.append(node)
            cg = node.get("child_graph")
            if isinstance(cg, dict):
                out.extend(_walk_nodes(cg.get("nodes") or []))
        return out

    all_nodes = _walk_nodes(nodes) if is_recursive_shape else [n for n in nodes if isinstance(n, dict)]
    impl = [
        n
        for n in all_nodes
        if isinstance(n, dict) and n.get("id") and _node_kind(n) == "composite"
    ]
    contracts = [n for n in all_nodes if isinstance(n, dict) and _node_kind(n) == "contract"]
    node_ids: set[str] = set()
    for n in nodes:
        if isinstance(n, dict) and n.get("id"):
            node_ids.add(str(n["id"]))

    min_impl = 1 if is_recursive_shape else 2
    if len(impl) < min_impl:
        errors.append(
            f"need at least {min_impl} implementation (non-contract) HLIG node(s); got {len(impl)}"
        )

    linked = 0
    for e in edges:
        if not isinstance(e, dict):
            continue
        u = e.get("from") or e.get("source")
        v = e.get("to") or e.get("target")
        if u and v and str(u) in node_ids and str(v) in node_ids:
            linked += 1
    if len(impl) >= 2 and linked == 0 and not is_recursive_shape:
        errors.append("need at least one HLIG edge between declared nodes (producer → contract → consumer)")

    if len(impl) >= 2 and len(contracts) < 1:
        errors.append('need at least one HLIG node with kind "contract" for shared cross-subsystem interfaces')

    return errors


def planner_contract_semantic_errors(hlig: dict[str, Any]) -> list[str]:
    """
    Enforce contract-first edge semantics:
    producer module -> contract node -> consumer module.
    """
    errs: list[str] = []
    if not isinstance(hlig, dict):
        return ["HLIG must be a JSON object."]
    if isinstance(hlig.get("graph"), dict):
        g = hlig.get("graph") or {}
        nodes = g.get("nodes") or []
        edges = g.get("edges") or []
    elif isinstance(hlig.get("hlig"), dict):
        g = hlig.get("hlig") or {}
        nodes = g.get("nodes") or []
        edges = g.get("edges") or []
    else:
        nodes = hlig.get("nodes") or []
        edges = hlig.get("edges") or []
    by_id: dict[str, dict] = {}
    for n in nodes:
        if isinstance(n, dict) and n.get("id"):
            by_id[str(n["id"])] = n

    out_map: dict[str, list[str]] = {}
    in_map: dict[str, list[str]] = {}
    for e in edges:
        if not isinstance(e, dict):
            continue
        u = e.get("from") or e.get("source")
        v = e.get("to") or e.get("target")
        if not u or not v:
            continue
        su, sv = str(u), str(v)
        out_map.setdefault(su, []).append(sv)
        in_map.setdefault(sv, []).append(su)

    for nid, node in by_id.items():
        if _node_kind(node) != "contract":
            continue
        producer = str(node.get("producer") or "").strip()
        raw_consumers = node.get("consumers") or node.get("implemented_by") or []
        consumers = [str(x).strip() for x in raw_consumers if str(x).strip()]
        if not producer and not consumers:
            # v2 contracts may rely on edges + source_of_truth without producer/consumer lists.
            continue
        if producer and producer in by_id:
            if nid not in out_map.get(producer, []):
                errs.append(
                    f"contract '{nid}' missing producer edge: expected '{producer}' -> '{nid}'"
                )
        for c in consumers:
            if c in by_id and c not in out_map.get(nid, []):
                errs.append(
                    f"contract '{nid}' missing consumer edge: expected '{nid}' -> '{c}'"
                )
        for pred in in_map.get(nid, []):
            if producer and pred != producer:
                errs.append(
                    f"contract '{nid}' has unexpected incoming edge '{pred}' -> '{nid}' (expected producer '{producer}')"
                )
        valid_succ = set(consumers) if consumers else set()
        for succ in out_map.get(nid, []):
            if valid_succ and succ not in valid_succ:
                errs.append(
                    f"contract '{nid}' has unexpected outgoing edge '{nid}' -> '{succ}' (not in consumers)"
                )
    return errs


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
        # _g stores the **control-flow** edges only. Data-flow edges are tracked separately
        # so that topological ordering is not affected by pure data dependencies.
        self._g = nx.DiGraph()
        self._data_edges: list[tuple[str, str, dict]] = []
        if nodes:
            for n in nodes:
                nid = n.get("id")
                if nid:
                    attrs = {k: v for k, v in n.items() if k != "id"}
                    # Infer node_type when not explicitly provided. This makes DTG node
                    # roles explicit for visualization and policy decisions.
                    if "node_type" not in attrs:
                        task_type = (attrs.get("task_type") or "").lower()
                        node_type = None
                        if task_type == "contract":
                            node_type = "contract"
                        elif task_type in ("design", "documentation"):
                            node_type = "design"
                        elif task_type == "scaffold":
                            node_type = "coding"
                        elif task_type in ("code", "integration", "build", "verification"):
                            node_type = "coding"
                        elif task_type in ("test", "unit_test", "integration_test", "system_test"):
                            node_type = "evaluation"
                        elif task_type in ("tool", "mcp_tool"):
                            node_type = "tool"
                        if node_type:
                            attrs["node_type"] = node_type
                    self._g.add_node(nid, **attrs)
        if edges:
            for e in edges:
                src = e.get("from") or e.get("source")
                tgt = e.get("to") or e.get("target")
                if src and tgt:
                    attrs = {k: v for k, v in e.items() if k not in ("from", "to", "source", "target")}
                    edge_type = (attrs.get("edge_type") or "").lower()
                    # Treat edges explicitly marked as data edges as data-flow only; they
                    # should not participate in control-flow topological order.
                    if edge_type == "data":
                        self._data_edges.append((src, tgt, attrs))
                    else:
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
        # Control-flow edges from the NetworkX DiGraph
        for u, v, data in self._g.edges(data=True):
            e = {"from": u, "to": v, **_safe_serialize(dict(data))}
            edges.append(e)
        # Data-flow edges tracked separately; include them in serialized form so
        # tools / visualizers can distinguish them (edge_type === "data").
        for u, v, data in self._data_edges:
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
        hlig_id = data.get("hlig_node_id", "") or data.get("parent_id", "")
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        inst = cls(hlig_id, nodes=nodes, edges=edges)
        _raise_if_cycle(inst._g, "DTG", hlig_node_id=hlig_id)
        return inst


class HLIGGraph:
    """High-Level Intent Graph - NetworkX DiGraph with optional DTG per node."""

    def __init__(self, nodes: list[dict] | None = None, edges: list[dict] | None = None):
        # _g stores control-flow edges only; data-flow edges can be represented
        # on edges with edge_type="data" and are kept out of the control graph.
        self._g = nx.DiGraph()
        self._data_edges: list[tuple[str, str, dict]] = []
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
                    edge_type = (attrs.get("edge_type") or "").lower()
                    # Data edges are stored separately; only control edges participate
                    # in topological order and causal parent calculations.
                    if edge_type == "data":
                        self._data_edges.append((src, tgt, attrs))
                    else:
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
        # Control-flow edges from the NetworkX DiGraph
        for u, v, data in self._g.edges(data=True):
            e = {"from": u, "to": v, **_safe_serialize(dict(data))}
            edges.append(e)
        # Data-flow edges are tracked separately (edge_type == "data") and
        # included for visualization / analysis but excluded from control flow.
        for u, v, data in getattr(self, "_data_edges", []):
            e = {"from": u, "to": v, **_safe_serialize(dict(data))}
            edges.append(e)
        return {"nodes": nodes, "edges": edges}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_planner_hlig(cls, planner_output: dict) -> "HLIGGraph | None":
        """Build HLIGGraph from planner artifact (hlig.nodes, hlig.edges)."""
        if not isinstance(planner_output, dict):
            return None
        recursive = planner_output.get("graph")
        if isinstance(recursive, dict) and recursive.get("nodes"):
            hlig_nodes: dict[str, dict] = {}
            hlig_edges: list[dict] = []
            dtg_by_owner: dict[str, dict] = {}

            def _edge_dict(e: dict) -> dict:
                out = {}
                for k, v in e.items():
                    if k in ("from", "to", "source", "target"):
                        continue
                    out[k] = v
                return out

            def _normalize_composite(node: dict) -> dict:
                n = dict(node)
                n.pop("child_graph", None)
                n.setdefault("kind", "composite")
                if "inputs" not in n:
                    n["inputs"] = list(n.get("inputs_required") or [])
                if "outputs" not in n:
                    n["outputs"] = list(n.get("outputs_produced") or [])
                return n

            def _to_dtg_member(node: dict) -> dict:
                n = dict(node)
                n.pop("child_graph", None)
                kind = _node_kind(n)
                if kind == "contract":
                    n.setdefault("task_type", "contract")
                else:
                    n.setdefault("task_type", "code")
                n.setdefault("title", n.get("task") or n.get("id") or "Untitled task")
                n.setdefault("description", n.get("description") or n.get("task") or n.get("title") or "")
                n["inputs_required"] = list(n.get("inputs_required") or n.get("inputs") or [])
                n["outputs_produced"] = list(n.get("outputs_produced") or n.get("outputs") or [])
                n.setdefault("dependencies", list(n.get("dependencies") or []))
                n.setdefault("success_criteria", list(n.get("success_criteria") or []))
                return n

            def _walk_graph(graph_obj: dict, owner_composite: str | None) -> None:
                nodes_local = [
                    n for n in (graph_obj.get("nodes") or []) if isinstance(n, dict) and n.get("id")
                ]
                by_id_local = {str(n["id"]): n for n in nodes_local}
                dtg_member_ids: set[str] = set()

                if owner_composite:
                    dtg_by_owner.setdefault(owner_composite, {"hlig_node_id": owner_composite, "nodes": [], "edges": []})

                for node in nodes_local:
                    nid = str(node["id"])
                    kind = _node_kind(node)
                    if kind in ("composite", "contract"):
                        normalized = dict(node)
                        if kind == "composite":
                            normalized = _normalize_composite(node)
                        else:
                            normalized.setdefault("kind", "contract")
                            normalized.setdefault("node_type", "contract")
                            normalized.setdefault("name", normalized.get("title") or normalized.get("id"))
                            sot = normalized.get("source_of_truth")
                            if isinstance(sot, dict):
                                normalized.setdefault("schema", sot)
                                normalized.setdefault("version", sot.get("version"))
                        hlig_nodes[nid] = normalized

                    if owner_composite and kind in ("atomic", "contract"):
                        dtg_member_ids.add(nid)
                        dtg_by_owner[owner_composite]["nodes"].append(_to_dtg_member(node))

                    child_graph = node.get("child_graph")
                    if kind == "composite" and isinstance(child_graph, dict):
                        _walk_graph(child_graph, nid)

                for e in graph_obj.get("edges") or []:
                    if not isinstance(e, dict):
                        continue
                    u = str(e.get("from") or e.get("source") or "")
                    v = str(e.get("to") or e.get("target") or "")
                    if not u or not v:
                        continue
                    ed = {"from": u, "to": v, **_edge_dict(e)}
                    if owner_composite and u in dtg_member_ids and v in dtg_member_ids:
                        dtg_by_owner[owner_composite]["edges"].append(ed)
                    else:
                        hlig_edges.append(ed)

            _walk_graph(recursive, None)

            if not hlig_nodes:
                return None
            node_list = list(hlig_nodes.values())
            node_ids = {str(n["id"]) for n in node_list if isinstance(n, dict) and n.get("id")}
            filtered_edges = [e for e in hlig_edges if e.get("from") in node_ids and e.get("to") in node_ids]
            control, data_edges = _split_hlig_control_and_data_edges(filtered_edges)
            fixed_control, removed = break_hlig_control_edges_until_dag(control, node_ids)
            if removed:
                planner_output["_hlig_cycle_edges_removed"] = removed
            inst = cls(nodes=node_list, edges=fixed_control + data_edges)
            _raise_if_cycle(inst._g, "HLIG")
            for owner, dtg_data in dtg_by_owner.items():
                if not inst._g.has_node(owner):
                    continue
                if not (dtg_data.get("nodes") or []):
                    continue
                inst.set_node_dtg(owner, DTGGraph.from_dict(dtg_data))
            return inst

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
            if e.get("edge_type") is not None:
                edge_attrs["edge_type"] = e["edge_type"]
            edges.append({"from": e["from"], "to": e["to"], **edge_attrs})
        node_ids = {str(n["id"]) for n in nodes if isinstance(n, dict) and n.get("id")}
        control, data_edges = _split_hlig_control_and_data_edges(edges)
        fixed_control, removed = break_hlig_control_edges_until_dag(control, node_ids)
        if removed:
            merged = fixed_control + data_edges
            hlig["edges"] = merged
            planner_output["_hlig_cycle_edges_removed"] = removed
        inst = cls(nodes=nodes, edges=fixed_control + data_edges)
        _raise_if_cycle(inst._g, "HLIG")
        return inst

    @classmethod
    def from_persisted_dict(cls, data: dict) -> "HLIGGraph":
        """
        Rebuild HLIGGraph from JSON saved by SessionManager.save_graph / HLIGGraph.to_dict().
        Re-attaches embedded DTGs as DTGGraph objects (constructor alone drops dtg from node attrs).
        """
        if not isinstance(data, dict):
            raise TypeError("from_persisted_dict expects a dict")
        if isinstance(data.get("graph"), dict):
            converted = cls.from_planner_hlig(data)
            if converted is not None:
                return converted
        nodes_raw = data.get("nodes") or []
        edges_raw = data.get("edges") or []
        if not nodes_raw:
            raise ValueError("persisted graph has no nodes")
        dtg_by_nid: dict[str, dict] = {}
        nodes_slim: list[dict] = []
        for n in nodes_raw:
            if not isinstance(n, dict) or not n.get("id"):
                continue
            nid = n["id"]
            nodes_slim.append({k: v for k, v in n.items() if k != "dtg"})
            dtg = n.get("dtg")
            if isinstance(dtg, dict) and dtg.get("nodes"):
                dtg_by_nid[str(nid)] = dtg
        inst = cls(nodes=nodes_slim, edges=edges_raw)
        _raise_if_cycle(inst._g, "HLIG")
        for nid, dtg_data in dtg_by_nid.items():
            if not inst._g.has_node(nid):
                continue
            dtg = DTGGraph.from_dict(dtg_data)
            inst.set_node_dtg(nid, dtg)
        return inst

    @classmethod
    def from_persisted_file(cls, path: str | Path) -> "HLIGGraph":
        """
        Load graph from a JSON file path. Accepts:
        - { \"nodes\": [...], \"edges\": [...] } (graph_*.json)
        - { \"hlig\": { nodes, edges } } (planner-style)
        - { \"hlig_graph\": { nodes, edges } } (session bundle)
        """
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Graph file not found: {p}")
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Graph file must contain a JSON object")
        payload: dict | None = None
        if raw.get("nodes"):
            payload = raw
        else:
            for key in ("graph", "hlig_graph", "hlig"):
                inner = raw.get(key)
                if isinstance(inner, dict) and inner.get("nodes"):
                    payload = {"graph": inner} if key == "graph" else inner
                    break
        if payload is None:
            raise ValueError(
                "Graph JSON must have top-level 'nodes' or a 'graph' / 'hlig' / 'hlig_graph' object with 'nodes'"
            )
        return cls.from_persisted_dict(payload)
