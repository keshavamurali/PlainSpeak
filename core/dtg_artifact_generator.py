"""
DTG Artifact Generator - traverses DTG nodes and generates design docs and code.

CVP (Causal Visual Programming) integration:
- Causal path traceability: records which HLIG nodes led to each artifact for audit
- Markov blanket scoping: restricts agent context to causal parents only
"""

import json
import re
from pathlib import Path
from typing import Any

from core.hlig_dtg_graphs import HLIGGraph, DTGGraph

try:
    from core.debug_logger import log_pipeline_event
except ImportError:
    log_pipeline_event = lambda *a, **kw: None

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# -----------------------------------------------------------------------------
# CVP: Metadata key for causal path in generated artifacts (traceability)
CAUSAL_PATH_FILE = "causal_path.json"


def _infer_framework(hlig_node: dict) -> str:
    """
    Infer framework from HLIG node.
    - node-react: frontend, UI, web pages, website
    - rust-tauri: backend, API, server, desktop
    """
    task = (hlig_node.get("task") or "").lower()
    interfaces = [str(x).lower() for x in hlig_node.get("external_interfaces", [])]
    lang = (hlig_node.get("language") or "TBD").lower()

    if "desktop" in task or "tauri" in lang or "rust" in lang:
        return "rust-tauri"
    if "frontend" in task or "ui" in task or "web page" in task or "react" in task:
        return "node-react"
    if "website" in task and "serve" not in task:
        return "node-react"
    if "backend" in task or "api" in task or "server" in task:
        return "rust-tauri"
    if "API" in interfaces or "DB" in interfaces:
        return "rust-tauri"
    return "node-react"  # default for web-related


def _topological_order(dtg: DTGGraph) -> list[dict]:
    """Return DTG nodes in topological order (designs before code)."""
    import networkx as nx

    d = dtg.to_dict()
    nodes = d.get("nodes", [])
    edges = d.get("edges", [])
    nodes_by_id = {n["id"]: n for n in nodes if n.get("id")}
    g = nx.DiGraph()
    for n in nodes:
        g.add_node(n["id"])
    for e in edges:
        src, tgt = e.get("from"), e.get("to")
        if src and tgt:
            g.add_edge(src, tgt)
    try:
        order = list(nx.topological_sort(g))
    except nx.NetworkXError:
        order = list(nodes_by_id.keys())
    return [nodes_by_id[nid] for nid in order if nid in nodes_by_id]


def _safe_filename(name: str) -> str:
    """Convert title to safe filename."""
    return re.sub(r"[^\w\-_]", "_", name).strip("_") or "untitled"


class DTGArtifactGenerator:
    """Generates design documents and code from DTG nodes."""

    def __init__(self, prompts_dir: Path | None = None):
        self.prompts_dir = prompts_dir or (PROJECT_ROOT / "prompts")

    def _load_prompt(self, name: str) -> str:
        path = self.prompts_dir / f"{name}.md"
        if path.exists():
            return path.read_text()
        return ""

    def _call_llm(self, prompt: str, input_data: dict, session_id: str = "") -> str:
        try:
            from core.model_manager import ModelManager
        except ImportError:
            return ""

        try:
            mm = ModelManager()
            full_prompt = f"{prompt.strip()}\n\n## Input\n\n```json\n{json.dumps(input_data, indent=2, default=str)}\n```"
            return mm.generate_text(full_prompt)
        except Exception as e:
            log_pipeline_event(session_id, "artifact_generation_error", {"error": str(e)})
            return ""

    def _generate_design_doc(
        self,
        node: dict,
        dependency_context: dict[str, str],
        session_id: str,
        causal_path: list[dict] | None = None,
        causal_parent_context: dict[str, str] | None = None,
    ) -> str:
        """
        Generate design document markdown for a DTG design node.
        CVP: causal_path and causal_parent_context restrict/annotate context (Markov blanket).
        """
        prompt = self._load_prompt("design_doc_generator")
        if not prompt:
            return ""

        input_data = {
            "dtg_node": {k: v for k, v in node.items() if k not in ("parent_hlig",)},
            "dependency_context": dependency_context,
        }
        if causal_path:
            input_data["causal_path"] = causal_path
        if causal_parent_context:
            input_data["causal_parent_context"] = causal_parent_context
        return self._call_llm(prompt, input_data, session_id).strip()

    def _generate_code(
        self,
        node: dict,
        framework: str,
        dependency_context: dict[str, str],
        session_id: str,
        causal_path: list[dict] | None = None,
        causal_parent_context: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """
        Generate code files for a DTG code node. Returns {path: content}.
        CVP: causal_path and causal_parent_context restrict/annotate context (Markov blanket).
        """
        prompt = self._load_prompt("code_generator")
        if not prompt:
            return {}

        input_data = {
            "dtg_node": {k: v for k, v in node.items() if k not in ("parent_hlig",)},
            "framework": framework,
            "dependency_context": dependency_context,
        }
        if causal_path:
            input_data["causal_path"] = causal_path
        if causal_parent_context:
            input_data["causal_parent_context"] = causal_parent_context
        response = self._call_llm(prompt, input_data, session_id)

        try:
            from core.json_parser import parse_llm_json
        except ImportError:
            return {}

        try:
            parsed = parse_llm_json(response)
            files = parsed.get("files", [])
            if not isinstance(files, list):
                return {}
            result = {}
            for f in files:
                if isinstance(f, dict) and f.get("path"):
                    result[f["path"]] = f.get("content", "")
            return result
        except Exception:
            return {}

    def _scaffold_project(self, hlig_dir: Path, framework: str, hlig_node: dict) -> None:
        """Create minimal package.json or Cargo.toml so project is buildable."""
        if framework == "rust-tauri":
            cargo = hlig_dir / "Cargo.toml"
            if not cargo.exists():
                name = _safe_filename(hlig_node.get("id", "app")).lower()
                cargo.write_text(f'''[package]
name = "{name}"
version = "0.1.0"
edition = "2021"

[dependencies]
''', encoding="utf-8")
        else:
            pkg = hlig_dir / "package.json"
            if not pkg.exists():
                pkg.write_text('''{
  "name": "hlig-generated",
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "node src/index.js || echo Add dev script",
    "build": "node --no-warnings src/index.js || echo Add build script",
    "start": "node src/index.js"
  }
}
''', encoding="utf-8")

    def _write_readme(self, hlig_dir: Path, hlig_node: dict, framework: str, generated_nodes: list[str]) -> None:
        """Write README with build instructions."""
        task = hlig_node.get("task", "Unknown task")
        lines = [
            f"# {hlig_node.get('id', 'HLIG')} — {task}",
            "",
            "This directory contains generated design documents and code for this HLIG node.",
            "",
            "## Generated Artifacts",
            "",
        ]
        for n in generated_nodes:
            lines.append(f"- {n}")
        lines.extend([
            "",
            "## Build Instructions",
            "",
        ])

        if framework == "rust-tauri":
            lines.extend([
                "### Prerequisites",
                "- Rust toolchain: https://rustup.rs/",
                "- For Tauri desktop: Node.js (for frontend), see https://tauri.app/",
                "",
                "### Build",
                "```bash",
                "cargo build",
                "```",
                "",
                "### Run",
                "```bash",
                "cargo run",
                "```",
                "",
                "### Test",
                "```bash",
                "cargo test",
                "```",
            ])
        else:
            lines.extend([
                "### Prerequisites",
                "- Node.js 18+ and npm",
                "",
                "### Install",
                "```bash",
                "npm install",
                "```",
                "",
                "### Development",
                "```bash",
                "npm run dev",
                "```",
                "",
                "### Build",
                "```bash",
                "npm run build",
                "```",
                "",
                "### Start",
                "```bash",
                "npm start",
                "```",
            ])

        (hlig_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")

    def _write_causal_path_metadata(self, hlig_dir: Path, causal_path: list[dict]) -> None:
        """CVP: Write causal path traceability metadata for audit/explainability."""
        path_file = hlig_dir / CAUSAL_PATH_FILE
        path_file.write_text(json.dumps({"causal_path": causal_path}, indent=2), encoding="utf-8")

    def generate_for_hlig(
        self,
        hlig_id: str,
        hlig_node: dict,
        dtg: DTGGraph,
        outputs_dir: Path,
        session_id: str,
        hlig_graph: HLIGGraph | None = None,
        causal_parent_context: dict[str, str] | None = None,
    ) -> Path | None:
        """
        Generate design docs and code for one HLIG node's DTG.
        CVP: causal_parent_context (Markov blanket) restricts context to causal parents only.
        Returns the HLIG subdirectory path or None on failure.
        """
        hlig_dir = outputs_dir / hlig_id
        hlig_dir.mkdir(parents=True, exist_ok=True)
        designs_dir = hlig_dir / "designs"
        designs_dir.mkdir(exist_ok=True)
        src_dir = hlig_dir / "src"
        src_dir.mkdir(exist_ok=True)

        # CVP: Compute causal path for traceability
        causal_path: list[dict] = []
        if hlig_graph:
            path_tuples = hlig_graph.get_causal_path(hlig_id)
            causal_path = [
                {"id": nid, "task": data.get("task", ""), "outputs": data.get("outputs", [])}
                for nid, data in path_tuples
            ]
            self._write_causal_path_metadata(hlig_dir, causal_path)

        framework = _infer_framework(hlig_node)
        order = _topological_order(dtg)
        resolved: dict[str, str] = {}
        generated: list[str] = []

        # Scaffold project structure so it's buildable
        self._scaffold_project(hlig_dir, framework, hlig_node)

        for node in order:
            nid = node.get("id", "")
            task_type = (node.get("task_type") or "").lower()
            deps = node.get("dependencies") or []
            # CVP: dependency_context is DTG-level; causal_parent_context is HLIG-level (Markov blanket)
            dep_ctx = {dep: resolved.get(dep, "") for dep in deps if resolved.get(dep)}

            if task_type in ("design", "documentation"):
                doc = self._generate_design_doc(
                    node, dep_ctx, session_id, causal_path=causal_path, causal_parent_context=causal_parent_context
                )
                if doc:
                    safe_name = _safe_filename(node.get("title", nid))
                    fp = designs_dir / f"{nid}_{safe_name}.md"
                    fp.write_text(doc, encoding="utf-8")
                    resolved[nid] = doc[:2000]  # truncate for context
                    generated.append(f"designs/{fp.name}")
                log_pipeline_event(session_id, "design_generated", {"node": nid})

            elif task_type in ("code", "integration", "test", "build", "verification"):
                files = self._generate_code(
                    node, framework, dep_ctx, session_id, causal_path=causal_path, causal_parent_context=causal_parent_context
                )
                for rel_path, content in files.items():
                    full_path = hlig_dir / rel_path
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(content, encoding="utf-8")
                    resolved[nid] = resolved.get(nid, "") + f"\n--- {rel_path} ---\n{content[:1500]}"
                    generated.append(rel_path)
                if files:
                    log_pipeline_event(session_id, "code_generated", {"node": nid, "files": list(files.keys())})

        self._write_readme(hlig_dir, hlig_node, framework, generated)
        return hlig_dir

    def generate_all(
        self,
        hlig_graph: HLIGGraph,
        session_id: str,
        date_dir: Path,
    ) -> Path | None:
        """
        Generate artifacts for all HLIG nodes with DTGs.
        CVP: Processes nodes in topological order; passes causal_parent_context (Markov blanket)
        from parent HLIG outputs to children for scoped reasoning.
        Creates outputs_{session_id}/ under date_dir.
        Returns the outputs directory path or None.
        """
        outputs_dir = date_dir / f"outputs_{session_id}"
        outputs_dir.mkdir(parents=True, exist_ok=True)

        # CVP: Process in topological order so parent outputs are available for Markov blanket
        topo_order = hlig_graph.topological_order()
        node_data_by_id = {nid: dict(data) for nid, data in hlig_graph.nodes()}
        hlig_outputs: dict[str, str] = {}

        for nid in topo_order:
            data = node_data_by_id.get(nid, {})
            dtg = data.get("dtg")
            if not isinstance(dtg, DTGGraph):
                continue
            hlig_node = {"id": nid, **{k: v for k, v in data.items() if k != "dtg" and not callable(v)}}

            # CVP: Build causal_parent_context from Markov blanket (only causal parents)
            causal_parent_ids = hlig_graph.get_causal_parents(nid)
            causal_parent_context = {pid: hlig_outputs.get(pid, "") for pid in causal_parent_ids if pid in hlig_outputs}

            try:
                result_path = self.generate_for_hlig(
                    nid, hlig_node, dtg, outputs_dir, session_id,
                    hlig_graph=hlig_graph,
                    causal_parent_context=causal_parent_context if causal_parent_context else None,
                )
                if result_path:
                    # Store summary for downstream Markov blanket scoping
                    task = hlig_node.get("task", "")
                    readme = result_path / "README.md"
                    summary = ""
                    if readme.exists():
                        summary = readme.read_text(encoding="utf-8")[:3000]
                    hlig_outputs[nid] = f"[{nid}] {task}\n{summary}"
            except Exception as e:
                log_pipeline_event(session_id, "artifact_generation_error", {"hlig": nid, "error": str(e)})

        return outputs_dir
