import { useCallback, useEffect, useMemo, useState } from "react";
import ReactFlow, {
  useNodesState,
  useEdgesState,
  Background,
  Controls,
  ReactFlowProvider,
  Handle,
  Position,
  BaseEdge,
  getSmoothStepPath,
  EdgeLabelRenderer,
} from "reactflow";
import dagre from "@dagrejs/dagre";
import "reactflow/dist/style.css";
import { runs } from "../api";

const getGraphData = (runData) => {
  const hligGraph = runData?.hlig_graph;
  const pg = runData?.plan_graph;
  const plannerOut = runData?.artifacts?.planner?.output;
  const hlig = plannerOut?.hlig;
  const causalPaths = runData?.causal_paths ?? {};

  let nodes = [];
  let edgesRaw = [];
  const hligEdges = hlig?.edges || hligGraph?.edges || [];

  if (hligGraph?.nodes?.length) {
    nodes = hligGraph.nodes.map((n) => ({
      id: n.id,
      description: n.task,
      agent: "coder",
      reads: n.inputs || [],
      writes: n.outputs || [],
      status: pg?.nodes?.find((pn) => pn.id === n.id)?.status ?? "pending",
      dtg: n.dtg,
      causal_path: causalPaths[n.id] ?? [],
    }));
    edgesRaw = (hligGraph.edges || []).map((e) => ({
      source: e.from ?? e.source,
      target: e.to ?? e.target,
      interface_type: e.interface_type ?? "dependency",
      causal: e.causal !== false,
    }));
  } else if (pg?.nodes?.length) {
    nodes = pg.nodes.map((n) => ({
      id: n.id,
      description: n.description ?? n.task,
      agent: n.agent ?? "coder",
      reads: n.reads ?? n.inputs ?? [],
      writes: n.writes ?? n.outputs ?? [],
      status: n.status ?? "pending",
      dtg: null,
      causal_path: causalPaths[n.id] ?? [],
    }));
    edgesRaw = (pg.edges || []).map((e) => ({
      source: e.source,
      target: e.target,
      interface_type: e.interface_type ?? "dependency",
      causal: e.causal !== false,
    }));
  } else if (hlig?.nodes?.length) {
    nodes = hlig.nodes.map((n) => ({
      id: n.id,
      description: n.task,
      agent: "coder",
      reads: n.inputs || [],
      writes: n.outputs || [],
      status: "pending",
      dtg: null,
      causal_path: causalPaths[n.id] ?? [],
    }));
    edgesRaw = hligEdges.map((e) => ({
      source: e.from,
      target: e.to,
      interface_type: e.interface_type,
      causal: e.causal !== false,
    }));
  }

  if (pg?.edges && !edgesRaw.length) {
    const hligMap = new Map(hligEdges.map((e) => [`${e.from}→${e.to}`, e]));
    edgesRaw = pg.edges.map((e) => {
      const hligE = hligMap.get(`${e.source}→${e.target}`);
      return {
        source: e.source,
        target: e.target,
        interface_type: e.interface_type ?? hligE?.interface_type ?? "dependency",
        causal: hligE ? hligE.causal !== false : true,
      };
    });
  }

  if (!nodes.length) return null;

  const edges = edgesRaw.map((e) => ({
    id: `e-${e.source}-${e.target}`,
    source: e.source,
    target: e.target,
    sourceHandle: e.source === "ROOT" ? "output" : "output",
    targetHandle: "input",
    type: "edgeWithLabel",
    style: { stroke: "#6366f1", strokeWidth: 2.5 },
    data: {
      interfaceType: e.interface_type || "dependency",
      sourceId: e.source,
      targetId: e.target,
      causal: e.causal !== false,
    },
  }));

  return { nodes, edges, artifactOutputsPath: runData?.artifact_outputs_path };
};

const getLayoutedElements = (nodes, edges, direction = "TB") => {
  const g = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep: 70, ranksep: 90 });

  nodes.forEach((n) => {
    const isRoot = n.id === "ROOT";
    g.setNode(n.id, { width: isRoot ? 70 : 240, height: isRoot ? 70 : 90 });
  });
  edges.forEach((e) => {
    g.setEdge(e.source, e.target);
  });

  dagre.layout(g);

  const layoutedNodes = nodes.map((n) => {
    const pos = g.node(n.id);
    const isRoot = n.id === "ROOT";
    const w = isRoot ? 70 : 240;
    const h = isRoot ? 70 : 90;
    return {
      ...n,
      position: { x: pos.x - w / 2, y: pos.y - h / 2 },
      data: { ...n.data, label: n.data?.label ?? n.id },
    };
  });

  return { nodes: layoutedNodes, edges };
};

const statusStyles = (status) => {
  switch (status) {
    case "completed":
      return "from-emerald-400/90 to-emerald-600/90 shadow-emerald-200/50 border-emerald-500/80";
    case "running":
      return "from-blue-400/90 to-blue-600/90 shadow-blue-200/50 border-blue-500/80";
    case "failed":
      return "from-red-400/90 to-red-600/90 shadow-red-200/50 border-red-500/80";
    default:
      return "from-slate-300 to-slate-400 shadow-slate-200/50 border-slate-400/60";
  }
};

function StartNode({ selected }) {
  return (
    <div
      className={`w-16 h-16 rounded-full flex items-center justify-center shadow-xl transition-all duration-300 relative ${
        selected ? "ring-4 ring-violet-400 ring-offset-2 scale-110" : "hover:scale-105"
      } bg-gradient-to-br from-violet-500 to-fuchsia-600 text-white font-bold text-sm border-2 border-white/30`}
    >
      <Handle id="output" type="source" position={Position.Bottom} className="!w-3 !h-3 !bg-white !border-2 !border-violet-600" />
      <span className="pointer-events-none">Start</span>
    </div>
  );
}

function GraphNode({ data, selected }) {
  const status = data?.status || "pending";
  const desc = data?.description || data?.id || "";
  const label = desc.length > 60 ? desc.slice(0, 57) + "…" : desc;
  const hasDTG = data?.dtg?.nodes?.length > 0;
  return (
    <div
      className={`min-w-[200px] max-w-[260px] rounded-2xl px-5 py-4 shadow-xl transition-all duration-300 hover:shadow-2xl hover:-translate-y-0.5 ${
        selected ? "ring-4 ring-violet-400 ring-offset-2 shadow-2xl" : ""
      } bg-gradient-to-br border-2 ${statusStyles(status)}`}
    >
      <Handle id="input" type="target" position={Position.Top} className="!w-3 !h-3 !bg-slate-300 !border-2 !border-white" />
      <Handle id="output" type="source" position={Position.Bottom} className="!w-3 !h-3 !bg-slate-300 !border-2 !border-white" />
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-bold text-white/90 uppercase tracking-wide">{data?.id}</div>
        {hasDTG && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/25 text-white/90 font-medium">
            {data.dtg.nodes.length} tasks
          </span>
        )}
      </div>
      <div className="text-sm text-white font-medium line-clamp-2 drop-shadow-sm mt-1">{label}</div>
      {data?.agent && (
        <div className="mt-2 text-xs text-white/70 font-medium">{data.agent}</div>
      )}
    </div>
  );
}

function EdgeWithLabel({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected }) {
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 16,
  });
  const [hovered, setHovered] = useState(false);
  const showLabel = hovered || selected;
  const isCausal = data?.causal !== false;
  const label = data?.interfaceType || "→";

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={{
          stroke: selected ? "#8b5cf6" : hovered ? "#6366f1" : "#6366f1",
          strokeWidth: selected ? 4 : hovered ? 3.5 : 2.5,
          filter: hovered || selected ? "drop-shadow(0 0 8px rgba(99,102,241,0.5))" : "none",
          transition: "stroke-width 0.2s, filter 0.2s",
        }}
      />
      <EdgeLabelRenderer>
        <div
          style={{
            position: "absolute",
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            pointerEvents: "all",
            padding: 16,
          }}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
          className="nodrag nopan cursor-pointer"
        >
          <div
            className={`px-2.5 py-1 rounded-lg text-xs font-semibold shadow-lg border transition-all duration-200 flex items-center gap-1.5 ${
              showLabel
                ? "opacity-100 scale-100 bg-violet-600 text-white border-violet-400/50"
                : "opacity-60 scale-90 bg-violet-500/70 text-white/90 border-violet-400/30"
            }`}
          >
            <span>{label}</span>
            {isCausal && (
              <span
                className="px-1 rounded text-[10px] bg-emerald-500/80"
                title="Causal edge (CVP)"
              >
                causal
              </span>
            )}
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

const dtgTaskTypeColors = {
  design: "from-amber-400 to-amber-600",
  code: "from-blue-400 to-blue-600",
  test: "from-emerald-400 to-emerald-600",
  integration: "from-cyan-400 to-cyan-600",
  documentation: "from-slate-400 to-slate-600",
  verification: "from-violet-400 to-violet-600",
  build: "from-orange-400 to-orange-600",
  review: "from-rose-400 to-rose-600",
};

function DTGNode({ data, selected }) {
  const tt = (data?.task_type || "").toLowerCase();
  const color = dtgTaskTypeColors[tt] || "from-slate-400 to-slate-500";
  const title = data?.title || data?.id || "";
  const shortTitle = title.length > 22 ? title.slice(0, 19) + "…" : title;
  return (
    <div
      className={`min-w-[100px] max-w-[140px] rounded-lg px-2.5 py-2 shadow-md border border-white/30 text-white text-xs relative ${
        selected ? "ring-2 ring-violet-400" : ""
      } bg-gradient-to-br ${color}`}
    >
      <Handle type="target" position={Position.Top} className="!w-2 !h-2 !bg-slate-400 !border-white" />
      <Handle type="source" position={Position.Bottom} className="!w-2 !h-2 !bg-slate-400 !border-white" />
      <div className="font-bold text-[10px] truncate" title={title}>{data?.id}</div>
      <div className="mt-0.5 font-medium line-clamp-2" title={title}>{shortTitle}</div>
      {tt && <div className="mt-1 text-[9px] uppercase opacity-90">{tt}</div>}
    </div>
  );
}

function DTGEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected }) {
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 8,
  });
  const [hovered, setHovered] = useState(false);
  const show = hovered || selected;
  const label = data?.dependencyType || data?.dependency_type || "→";

  return (
    <>
      <BaseEdge id={id} path={edgePath} style={{
        stroke: selected ? "#8b5cf6" : "#64748b",
        strokeWidth: selected ? 3 : 2,
      }} />
      <EdgeLabelRenderer>
        <div
          style={{
            position: "absolute",
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            pointerEvents: "all",
            padding: 12,
          }}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
          className="nodrag nopan cursor-pointer"
        >
          <div
            className={`px-2 py-0.5 rounded text-[10px] font-medium transition-all ${
              show ? "bg-slate-600 text-white" : "bg-slate-400/70 text-white/90"
            }`}
          >
            {label}
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

const getDTGLayout = (dtgNodes, dtgEdges) => {
  const g = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "TB", nodesep: 24, ranksep: 40 });
  dtgNodes.forEach((n) => g.setNode(n.id, { width: 120, height: 48 }));
  dtgEdges.forEach((e) => g.setEdge(e.source || e.from, e.target || e.to));
  dagre.layout(g);

  return dtgNodes.map((n) => {
    const pos = g.node(n.id);
    return {
      id: n.id,
      position: pos ? { x: pos.x - 60, y: pos.y - 24 } : { x: 0, y: 0 },
      data: n,
    };
  });
};

const dtgNodeTypes = { default: DTGNode };
const dtgEdgeTypes = { default: DTGEdge };

function DTGSubGraph({ dtg }) {
  const [selectedDtgNode, setSelectedDtgNode] = useState(null);
  const [selectedDtgEdge, setSelectedDtgEdge] = useState(null);
  const [expanded, setExpanded] = useState(false);

  const { flowNodes, flowEdges } = useMemo(() => {
    if (!dtg?.nodes?.length) return { flowNodes: [], flowEdges: [] };
    const nodes = dtg.nodes;
    const rawEdges = dtg.edges || [];
    const edges = rawEdges.map((e) => ({
      id: `dtg-e-${e.from ?? e.source}-${e.to ?? e.target}`,
      source: String(e.from ?? e.source),
      target: String(e.to ?? e.target),
      type: "default",
      data: {
        dependencyType: e.dependency_type,
        description: e.description,
        sourceId: e.from ?? e.source,
        targetId: e.to ?? e.target,
      },
    }));
    const layouted = getDTGLayout(
      nodes.map((n) => ({ ...n, id: String(n.id) })),
      edges
    );
    const flowNodes = layouted.map((n) => ({
      id: n.id,
      type: "default",
      position: n.position,
      data: n.data,
      draggable: false,
    }));
    const flowEdges = edges.map((e) => ({
      ...e,
      selected: selectedDtgEdge ? e.id === selectedDtgEdge.id : false,
    }));
    return { flowNodes, flowEdges };
  }, [dtg, selectedDtgEdge?.id]);

  const selectedDtgNodeData = selectedDtgNode
    ? dtg?.nodes?.find((n) => n.id === selectedDtgNode.id)
    : null;
  const selectedDtgEdgeData = selectedDtgEdge?.data;

  if (!dtg?.nodes?.length) return null;

  const graphContent = (
    <ReactFlowProvider>
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        onNodeClick={(_, node) => {
          setSelectedDtgNode(node);
          setSelectedDtgEdge(null);
        }}
        onEdgeClick={(_, edge) => {
          setSelectedDtgEdge(edge);
          setSelectedDtgNode(null);
        }}
        onPaneClick={() => {
          setExpanded((e) => !e);
          setSelectedDtgNode(null);
          setSelectedDtgEdge(null);
        }}
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1.5 }}
        nodesDraggable={false}
        nodesConnectable={false}
        nodeTypes={dtgNodeTypes}
        edgeTypes={dtgEdgeTypes}
        defaultEdgeOptions={{ type: "default" }}
        proOptions={{ hideAttribution: true }}
        style={{ width: "100%", height: "100%" }}
      >
        <Background color="#94a3b8" gap={12} size={1} />
        <Controls className="!bg-white !rounded !shadow !border !border-slate-200 !scale-90" />
      </ReactFlow>
    </ReactFlowProvider>
  );

  return (
    <div className="mt-4 pt-3 border-t border-slate-200">
      <h4 className="text-xs font-semibold text-slate-600 mb-2 uppercase tracking-wide flex items-center justify-between">
        <span>DTG Graph ({dtg.nodes.length} tasks, {dtg.edges?.length || 0} edges)</span>
        <span className="text-slate-400 font-normal normal-case">Click graph to {expanded ? "shrink" : "expand"}</span>
      </h4>
      {!expanded && (
        <div
          className="h-80 rounded-lg border border-slate-200 overflow-hidden bg-slate-50 cursor-zoom-in"
          style={{ minHeight: 320 }}
        >
          {graphContent}
        </div>
      )}
      {expanded && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => setExpanded(false)}
        >
          <div
            className="bg-white rounded-xl shadow-2xl overflow-hidden flex flex-col w-[95vw] h-[90vh] max-w-7xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-2 border-b border-slate-200 bg-slate-50 shrink-0">
              <span className="text-sm font-semibold text-slate-700">DTG Graph (expanded)</span>
              <button
                type="button"
                onClick={() => setExpanded(false)}
                className="px-3 py-1.5 text-sm font-medium rounded-lg bg-slate-200 hover:bg-slate-300 text-slate-700"
              >
                Close
              </button>
            </div>
            <div className="flex-1 min-h-0 relative">
              {graphContent}
            </div>
            {(selectedDtgNodeData || selectedDtgEdgeData) && (
              <div className="border-t border-slate-200 p-3 bg-slate-50 shrink-0 max-h-40 overflow-y-auto">
                {selectedDtgNodeData ? (
                  <>
                    <div className="font-bold text-slate-800 text-sm">{selectedDtgNodeData.id}</div>
                    <div className="font-medium mt-0.5 text-slate-700 text-sm">{selectedDtgNodeData.title}</div>
                    {selectedDtgNodeData.task_type && (
                      <div className="text-slate-500 mt-0.5 text-sm">Type: {selectedDtgNodeData.task_type}</div>
                    )}
                    {selectedDtgNodeData.description && (
                      <div className="mt-1 text-slate-600 text-sm" title={selectedDtgNodeData.description}>
                        {selectedDtgNodeData.description}
                      </div>
                    )}
                  </>
                ) : selectedDtgEdgeData ? (
                  <>
                    <div className="font-bold text-slate-800 text-sm">
                      {selectedDtgEdgeData.sourceId} → {selectedDtgEdgeData.targetId}
                    </div>
                    <div className="text-slate-600 mt-0.5 text-sm">
                      Type: {selectedDtgEdgeData.dependencyType || "dependency"}
                    </div>
                    {selectedDtgEdgeData.description && (
                      <div className="mt-1 text-slate-600 text-sm" title={selectedDtgEdgeData.description}>
                        {selectedDtgEdgeData.description}
                      </div>
                    )}
                  </>
                ) : null}
              </div>
            )}
          </div>
        </div>
      )}
      {(selectedDtgNodeData || selectedDtgEdgeData) && (
        <div className="mt-2 p-2 rounded bg-slate-100 border border-slate-200 text-xs overflow-y-auto max-h-32">
          {selectedDtgNodeData ? (
            <>
              <div className="font-bold text-slate-800">{selectedDtgNodeData.id}</div>
              <div className="font-medium mt-0.5 text-slate-700">{selectedDtgNodeData.title}</div>
              {selectedDtgNodeData.task_type && (
                <div className="text-slate-500 mt-0.5">Type: {selectedDtgNodeData.task_type}</div>
              )}
              {selectedDtgNodeData.description && (
                <div className="mt-1 text-slate-600 line-clamp-2" title={selectedDtgNodeData.description}>
                  {selectedDtgNodeData.description}
                </div>
              )}
            </>
          ) : selectedDtgEdgeData ? (
            <>
              <div className="font-bold text-slate-800">
                {selectedDtgEdgeData.sourceId} → {selectedDtgEdgeData.targetId}
              </div>
              <div className="text-slate-600 mt-0.5">
                Type: {selectedDtgEdgeData.dependencyType || "dependency"}
              </div>
              {selectedDtgEdgeData.description && (
                <div className="mt-1 text-slate-600" title={selectedDtgEdgeData.description}>
                  {selectedDtgEdgeData.description}
                </div>
              )}
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}

const nodeTypes = { graphNode: GraphNode, startNode: StartNode };
const edgeTypes = { edgeWithLabel: EdgeWithLabel };

function HLIGGraphInner({ runId, runData: initialRunData, onSelectRun }) {
  const [runData, setRunData] = useState(initialRunData);
  const [selectedNode, setSelectedNode] = useState(null);
  const [selectedEdge, setSelectedEdge] = useState(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  useEffect(() => {
    setRunData(initialRunData);
  }, [initialRunData]);

  useEffect(() => {
    if (!runId) {
      setNodes([]);
      setEdges([]);
      setSelectedNode(null);
      return;
    }
    const load = async () => {
      try {
        const d = await runs.get(runId);
        setRunData(d);
      } catch (e) {
        console.error("Load run failed:", e);
      }
    };
    load();
  }, [runId]);

  useEffect(() => {
    const graph = getGraphData(runData);
    if (!graph) {
      setNodes([]);
      setEdges([]);
      setSelectedNode(null);
      setSelectedEdge(null);
      return;
    }

    setSelectedNode(null);
    setSelectedEdge(null);
    const flowNodes = graph.nodes.map((n) => ({
      id: n.id,
      type: n.id === "ROOT" ? "startNode" : "graphNode",
      data: {
        ...n,
        id: n.id,
        label: n.id === "ROOT" ? "Start" : n.id,
      },
    }));
    const { nodes: layouted, edges: layoutedEdges } = getLayoutedElements(
      flowNodes,
      graph.edges
    );
    setNodes(layouted);
    setEdges(layoutedEdges);
  }, [runData]);

  useEffect(() => {
    setEdges((eds) =>
      eds.map((e) => ({ ...e, selected: selectedEdge ? e.id === selectedEdge.id : false }))
    );
  }, [selectedEdge]);

  const onNodeClick = useCallback((_, node) => {
    setSelectedNode(node);
    setSelectedEdge(null);
  }, []);

  const onEdgeClick = useCallback((_, edge) => {
    setSelectedEdge(edge);
    setSelectedNode(null);
  }, []);

  const onPaneClick = useCallback(() => {
    setSelectedNode(null);
    setSelectedEdge(null);
  }, []);

  const graph = getGraphData(runData);
  const artifactOutputsPath = graph?.artifactOutputsPath;
  const nodeDetails = selectedNode
    ? graph?.nodes.find((n) => n.id === selectedNode.id)
    : null;
  const edgeDetails = selectedEdge?.data;
  const dtgForSelected =
    nodeDetails?.dtg ??
    (selectedNode &&
      runData?.hlig_graph?.nodes?.find((n) => n.id === selectedNode.id)?.dtg);

  const projectInfo = runData?.artifacts?.planner?.output?.project;

  return (
    <main className="mx-auto max-w-6xl flex flex-col h-[calc(100vh-72px)] bg-white rounded-lg shadow-sm border border-slate-200 overflow-hidden">
      <div className="flex-shrink-0 px-4 py-3 border-b border-slate-200 bg-slate-50 flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-medium text-slate-700">HLIG Graph</h2>
            {runId && (
              <p className="text-xs text-slate-500 mt-0.5 truncate" title={runId}>
                Run: {runId}
              </p>
            )}
          </div>
          {!runId && (
            <p className="text-sm text-slate-500">
              Select a run from the Runs tab to view its graph
            </p>
          )}
        </div>
        {artifactOutputsPath && (
          <div className="flex items-center gap-2">
            <span className="text-slate-500 text-xs font-medium shrink-0">Outputs:</span>
            <code
              className="text-xs text-slate-700 bg-white/80 px-2 py-1 rounded border border-slate-200 truncate font-mono"
              title={artifactOutputsPath}
            >
              {artifactOutputsPath}
            </code>
          </div>
        )}
      </div>

      {!runId ? (
        <div className="flex-1 flex items-center justify-center text-slate-500">
          <p>No run selected. Go to Runs and open a run to see its HLIG graph.</p>
        </div>
      ) : !graph ? (
        <div className="flex-1 flex items-center justify-center text-slate-500">
          <p>No graph data yet. The planner may still be running or no plan was produced.</p>
        </div>
      ) : (
        <div className="flex-1 flex min-h-0">
          <div className="flex-1 min-w-0" style={{ minHeight: 400 }}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeClick={onNodeClick}
              onEdgeClick={onEdgeClick}
              onPaneClick={onPaneClick}
              fitView
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              defaultEdgeOptions={{ type: "edgeWithLabel" }}
              proOptions={{ hideAttribution: true }}
              className="bg-gradient-to-br from-slate-50 to-violet-50/30"
            >
              <Background color="#94a3b8" gap={20} size={1} />
              <Controls className="!bg-white !rounded-lg !shadow-md !border !border-slate-200" />
            </ReactFlow>
          </div>

          {(selectedNode && nodeDetails) || (selectedEdge && edgeDetails) ? (
            <div
              className={`flex-shrink-0 border-l border-slate-200 bg-slate-50 overflow-y-auto p-4 ${
                dtgForSelected ? "w-[420px]" : "w-80"
              }`}
            >
              {selectedNode && nodeDetails ? (
                <>
              <h3 className="text-sm font-semibold text-slate-800 mb-3">Node Details</h3>
              <div className="space-y-3 text-sm">
                <div>
                  <span className="text-slate-500 font-medium">ID</span>
                  <p className="text-slate-800 font-mono">{nodeDetails.id}</p>
                </div>
                {nodeDetails.description && (
                  <div>
                    <span className="text-slate-500 font-medium">Task</span>
                    <p className="text-slate-800 whitespace-pre-wrap">{nodeDetails.description}</p>
                  </div>
                )}
                {nodeDetails.agent && (
                  <div>
                    <span className="text-slate-500 font-medium">Agent</span>
                    <p className="text-slate-800">{nodeDetails.agent}</p>
                  </div>
                )}
                {nodeDetails.status && (
                  <div>
                    <span className="text-slate-500 font-medium">Status</span>
                    <p className="text-slate-800 capitalize">{nodeDetails.status}</p>
                  </div>
                )}
                {nodeDetails.reads?.length > 0 && (
                  <div>
                    <span className="text-slate-500 font-medium">Inputs</span>
                    <ul className="list-disc list-inside text-slate-800">
                      {nodeDetails.reads.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {nodeDetails.writes?.length > 0 && (
                  <div>
                    <span className="text-slate-500 font-medium">Outputs</span>
                    <ul className="list-disc list-inside text-slate-800">
                      {nodeDetails.writes.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {nodeDetails.output != null && (
                  <div>
                    <span className="text-slate-500 font-medium">Output</span>
                    <div className="text-slate-800 text-xs mt-1 p-2 bg-white rounded border border-slate-200 max-h-40 overflow-y-auto">
                      {typeof nodeDetails.output === "string" ? (
                        nodeDetails.output
                      ) : (
                        <pre className="whitespace-pre-wrap">
                          {JSON.stringify(nodeDetails.output, null, 2)}
                        </pre>
                      )}
                    </div>
                  </div>
                )}
                {nodeDetails.error && (
                  <div>
                    <span className="text-slate-500 font-medium">Error</span>
                    <p className="text-red-700 text-xs">{nodeDetails.error}</p>
                  </div>
                )}
                {nodeDetails.causal_path?.length > 0 && (
                  <div>
                    <span className="text-slate-500 font-medium">Causal Path (CVP)</span>
                    <p className="text-slate-600 text-xs mt-1">
                      Nodes that led to this one (traceability):
                    </p>
                    <ol className="mt-2 space-y-1.5 text-xs">
                      {nodeDetails.causal_path.map((cp, i) => (
                        <li key={cp.id || i} className="flex items-start gap-2">
                          <span className="shrink-0 font-mono text-violet-600">{cp.id}</span>
                          <span className="text-slate-700 line-clamp-1" title={cp.task}>
                            {cp.task || "—"}
                          </span>
                        </li>
                      ))}
                    </ol>
                  </div>
                )}
                {dtgForSelected && (
                  <DTGSubGraph dtg={dtgForSelected} />
                )}
              </div>
                </>
              ) : selectedEdge && edgeDetails ? (
                <>
                  <h3 className="text-sm font-semibold text-slate-800 mb-3">Edge Details</h3>
                  <div className="space-y-3 text-sm">
                    <div>
                      <span className="text-slate-500 font-medium">Connection</span>
                      <p className="text-slate-800 font-mono text-xs mt-1">
                        {edgeDetails.sourceId} → {edgeDetails.targetId}
                      </p>
                    </div>
                    <div>
                      <span className="text-slate-500 font-medium">Interface Type</span>
                      <p className="text-slate-800 font-medium mt-1">{edgeDetails.interfaceType}</p>
                    </div>
                    {edgeDetails.causal !== false && (
                      <div>
                        <span className="text-slate-500 font-medium">CVP</span>
                        <p className="text-slate-800 mt-1">
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-100 text-emerald-800">
                            Causal edge
                          </span>
                          <span className="text-slate-500 ml-1 text-xs">
                            Source directly causes target
                          </span>
                        </p>
                      </div>
                    )}
                  </div>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      )}

      {projectInfo && (
        <div className="flex-shrink-0 px-4 py-2 border-t border-slate-200 bg-white text-sm">
          <span className="text-slate-500">Project: </span>
          <span className="font-medium text-slate-800">{projectInfo.name}</span>
          {projectInfo.description && (
            <span className="text-slate-600 ml-2">— {projectInfo.description}</span>
          )}
        </div>
      )}
    </main>
  );
}

export default function HLIGGraphPage({ runId, runData, onSelectRun }) {
  return (
    <ReactFlowProvider>
      <HLIGGraphInner
        runId={runId}
        runData={runData}
        onSelectRun={onSelectRun}
      />
    </ReactFlowProvider>
  );
}
