# PlainSpeak Tools

## Graph Viewer

A standalone HTML utility to view HLIG/DTG graph JSON files **without running the backend**.

### Usage

1. **Open in browser**: Open `graph-viewer.html` directly (double-click or `open tools/graph-viewer.html`)
2. Click **"Load graph JSON"** and select a graph file (e.g. `session_log/sessions/2026/03/05/graph_2706240600.json`)
   - Quick demo file included: `tools/sample_recursive_graph.json`
3. Use the **dropdown** to switch between **HLIG** (high-level tasks) and **Child graph: HLIG-N** (nested graph for each composite node)
4. Click nodes to see details in the sidebar

Accepts:
- Raw HLIG: `{ "nodes": [...], "edges": [...] }`
- Recursive v2: `{ "graph": { "nodes": [...], "edges": [...] } }`
- Wrapped: `{ "hlig_graph": { "nodes": [...], "edges": [...] } }`

### Frontend integration

The main app (`frontend`) also supports file loading on the Graph tab:
- Run `npm run dev` in the frontend folder (no backend needed for graph viewing)
- Go to the **Graph** tab → click **"Load from file"** → select a graph JSON
