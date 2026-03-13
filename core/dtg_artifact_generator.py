"""
DTG Artifact Generator - traverses DTG nodes and generates design docs and code.

CVP (Causal Visual Programming) integration:
- Causal path traceability: records which HLIG nodes led to each artifact for audit
- Markov blanket scoping: restricts agent context to causal parents only
"""

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from core.hlig_dtg_graphs import HLIGGraph, DTGGraph

try:
    from core.debug_logger import log_pipeline_event, CostLimitExceeded, check_cost_limit_before_llm, log_llm_input
except ImportError:
    log_pipeline_event = lambda *a, **kw: None
    CostLimitExceeded = Exception  # noqa: type to satisfy isinstance
    def check_cost_limit_before_llm(_session_id: str) -> None: ...
    log_llm_input = lambda *a, **kw: None

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Configurable context truncation (env: DESIGN_CONTEXT_MAX_CHARS, CODE_CONTEXT_MAX_CHARS). Reduces input tokens.
# Use 0 for no truncation.
def _ctx_limit(name: str, default: int) -> int:
    v = os.environ.get(name, "")
    return int(v) if v else default


_DESIGN_CTX = _ctx_limit("DESIGN_CONTEXT_MAX_CHARS", 2000)
_CODE_CTX = _ctx_limit("CODE_CONTEXT_MAX_CHARS", 1500)


def _truncate_design(s: str) -> str:
    return s[:_DESIGN_CTX] if _DESIGN_CTX > 0 else s


def _truncate_code(s: str) -> str:
    return s[:_CODE_CTX] if _CODE_CTX > 0 else s


def _make_code_output_spec(node_id: str, files: list[tuple[str, str]]) -> str:
    """Build canonical code_output JSON for LLM consumption (dependency context)."""
    spec = {
        "type": "code_output",
        "version": "1.0",
        "node_id": node_id,
        "files": [
            {"path": p, "content_preview": _truncate_code(c)}
            for p, c in files
        ],
    }
    return json.dumps(spec, indent=2, default=str)


def _make_dtg_node_ref(dep_node: dict) -> str:
    """Build canonical dtg_node_ref JSON when design spec is missing (e.g. hlig_no_design_docs)."""
    ref = {
        "type": "dtg_node_ref",
        "version": "1.0",
        "node_id": dep_node.get("id", ""),
        "title": dep_node.get("title", ""),
        "description": dep_node.get("description", ""),
        "inputs_required": dep_node.get("inputs_required", []),
        "outputs_produced": dep_node.get("outputs_produced", []),
        "success_criteria": dep_node.get("success_criteria", []),
    }
    return json.dumps(ref, indent=2, default=str)


def _build_dep_ctx(deps: list[str], resolved: dict[str, str], nodes_by_id: dict[str, dict]) -> dict[str, str]:
    """Build dependency_context with canonical formats. Injects dtg_node_ref when design spec is missing."""
    result: dict[str, str] = {}
    for dep in deps:
        content = resolved.get(dep)
        if content:
            result[dep] = content
            continue
        node = nodes_by_id.get(dep)
        if node and (node.get("task_type") or "").lower() in ("design", "documentation"):
            result[dep] = _make_dtg_node_ref(node)
    return result


def _build_implementation_brief(
    dependency_context: dict[str, str],
    interface_definitions: list[dict] | None,
    dtg_node: dict,
    framework: str,
) -> str:
    """
    Build a full LLM-oriented implementation brief from design/DTG context.
    Gives the coder a single, clear prompt block: what to implement, interfaces, and compilability.
    """
    lines: list[str] = []
    lines.append("## Implementation brief (follow this when generating code)")
    lines.append("")
    lines.append(f"**This task:** {dtg_node.get('title', '')} — {dtg_node.get('description', '')}")
    lines.append("")
    for dep_id, content in dependency_context.items():
        if not content or not content.strip():
            continue
        try:
            parsed = json.loads(content)
        except Exception:
            lines.append(f"### Dependency {dep_id}")
            lines.append(content[:2000] + ("..." if len(content) > 2000 else ""))
            lines.append("")
            continue
        ptype = parsed.get("type", "")
        if ptype == "design_spec":
            lines.append(f"### Design spec ({dep_id})")
            arch = parsed.get("architecture") or {}
            if arch:
                lines.append("**Architecture:** " + json.dumps(arch, indent=2, default=str))
            instr = parsed.get("implementation_instructions") or []
            if instr:
                lines.append("**Implementation steps (follow in order):**")
                for i, step in enumerate(instr, 1):
                    lines.append(f"  {i}. {step}")
            constraints = parsed.get("constraints") or []
            if constraints:
                lines.append("**Constraints:** " + "; ".join(constraints))
            outputs = parsed.get("outputs") or []
            if outputs:
                lines.append("**Outputs to produce:** " + ", ".join(str(o) for o in outputs))
            iface_refs = parsed.get("interface_refs") or []
            if iface_refs:
                lines.append("**Interface refs:** " + ", ".join(str(r) for r in iface_refs))
            lines.append("")
        elif ptype == "dtg_node_ref":
            lines.append(f"### DTG ref ({dep_id}) — use when no full design spec")
            lines.append(f"**Title:** {parsed.get('title', '')}")
            lines.append(f"**Description:** {parsed.get('description', '')}")
            for key in ("inputs_required", "outputs_produced", "success_criteria"):
                val = parsed.get(key)
                if val:
                    lines.append(f"**{key}:** {json.dumps(val, default=str)}")
            lines.append("")
        # code_output: no need to repeat in brief; dependency_context already has it
    if interface_definitions:
        lines.append("### Required interfaces (APIs / contracts)")
        lines.append("Implement and respect these contracts; both Frontend and Backend use the same definitions.")
        lines.append(json.dumps(interface_definitions, indent=2, default=str))
        lines.append("")
    lines.append("### Compilation requirement")
    if framework == "rust-tauri":
        lines.append("Code must compile with `cargo build`. Use valid Rust 2021; all imports and types must resolve.")
    else:
        lines.append("Code must build with `npm run build`. Use valid JS/ES modules; all imports must resolve.")
    return "\n".join(lines)


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
    lang = (hlig_node.get("language") or "Rust, Tauri, React, CSS").lower()

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
    return "rust-tauri"  # default: Rust, Tauri, React, CSS


def _run_local_build(hlig_dir: Path, framework: str, timeout_sec: int = 120) -> tuple[bool, str, str]:
    """
    Run a lightweight compile/build in hlig_dir. No MCP.
    Returns (success, stdout, stderr).
    """
    if not hlig_dir.exists():
        return False, "", "directory does not exist"
    try:
        if framework == "rust-tauri":
            r = subprocess.run(
                ["cargo", "build"],
                cwd=str(hlig_dir),
                capture_output=True,
                timeout=timeout_sec,
                text=True,
            )
            return r.returncode == 0, r.stdout or "", r.stderr or ""
        # node-react
        subprocess.run(
            ["npm", "install"],
            cwd=str(hlig_dir),
            capture_output=True,
            timeout=timeout_sec,
            text=True,
        )
        r = subprocess.run(
            ["npm", "run", "build"],
            cwd=str(hlig_dir),
            capture_output=True,
            timeout=timeout_sec,
            text=True,
        )
        return r.returncode == 0, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return False, "", "build timed out"
    except FileNotFoundError:
        return False, "", "cargo or npm not found"
    except Exception as e:
        return False, "", str(e)


BUILD_LOG_FILE = "build.log"


def _write_build_log(hlig_dir: Path, success: bool, stdout: str, stderr: str) -> None:
    """Append build result and output to build.log in the HLIG code directory."""
    log_path = hlig_dir / BUILD_LOG_FILE
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    status = "success" if success else "failure"
    block = f"\n{'='*60}\n[{ts}] Build {status}\n{'='*60}\n"
    if stdout:
        block += f"--- stdout ---\n{stdout}\n"
    if stderr:
        block += f"--- stderr ---\n{stderr}\n"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(block)
    except Exception:
        pass


def _topological_order(dtg: DTGGraph) -> list[dict]:
    """Return DTG nodes in topological order (designs before code)."""
    import networkx as nx

    try:
        from networkx.exception import NetworkXUnfeasible
    except ImportError:
        NetworkXUnfeasible = type("NetworkXUnfeasible", (Exception,), {})

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
    except (nx.NetworkXError, NetworkXUnfeasible):
        order = list(nodes_by_id.keys())
    return [nodes_by_id[nid] for nid in order if nid in nodes_by_id]


def _safe_filename(name: str) -> str:
    """Convert title to safe filename."""
    return re.sub(r"[^\w\-_]", "_", name).strip("_") or "untitled"


def _get_interfaces_for_hlig(hlig_graph: HLIGGraph, hlig_id: str) -> list[dict]:
    """Extract interface definitions for edges involving the given HLIG node."""
    result: list[dict] = []
    for u, v, data in hlig_graph.edges():
        if u != hlig_id and v != hlig_id:
            continue
        spec = data.get("interface_spec")
        ref = data.get("interface_ref")
        if spec and isinstance(spec, dict):
            result.append({
                "from": u, "to": v,
                "interface_type": data.get("interface_type", "dependency"),
                **{k: v for k, v in spec.items() if k in ("type", "description", "endpoints", "schema", "ref")},
            })
        elif ref:
            result.append({"from": u, "to": v, "interface_ref": ref, "interface_type": data.get("interface_type", "dependency")})
    return result


def _write_interface_definitions(hlig_graph: HLIGGraph, outputs_dir: Path) -> None:
    """
    Extract interface_spec from HLIG edges and write to shared/interfaces.json.
    Both Frontend and Backend can read this file during code generation.
    """
    by_edge: dict[str, dict] = {}
    for u, v, data in hlig_graph.edges():
        spec = data.get("interface_spec")
        ref = data.get("interface_ref")
        if spec and isinstance(spec, dict):
            key = f"{u}→{v}"
            by_edge[key] = {
                "from": u,
                "to": v,
                "interface_type": data.get("interface_type", "dependency"),
                **{k: v for k, v in spec.items() if k in ("type", "description", "endpoints", "schema", "ref")},
            }
        elif ref:
            key = f"{u}→{v}"
            by_edge[key] = {"from": u, "to": v, "interface_ref": ref, "interface_type": data.get("interface_type", "dependency")}
    if not by_edge:
        return
    shared_dir = outputs_dir / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    out = {"by_edge": by_edge}
    (shared_dir / "interfaces.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")


class DTGArtifactGenerator:
    """Generates design documents and code from DTG nodes."""

    def __init__(self, prompts_dir: Path | None = None):
        self.prompts_dir = prompts_dir or (PROJECT_ROOT / "prompts")

    def _load_prompt(self, name: str) -> str:
        path = self.prompts_dir / f"{name}.md"
        if path.exists():
            return path.read_text()
        return ""

    def _call_llm(self, prompt: str, input_data: dict, session_id: str = "", agent_name: str = "artifact_gen") -> str:
        check_cost_limit_before_llm(session_id)
        log_llm_input(session_id, agent_name, input_data)
        try:
            from core.model_manager import ModelManager
            from core.debug_logger import log_llm_call
        except ImportError:
            return ""

        try:
            mm = ModelManager()
            variable_input = json.dumps(input_data, indent=2, default=str)
            full_prompt = f"{prompt.strip()}\n\n## Input\n\n```json\n{variable_input}\n```"
            text, usage = mm.generate_text(full_prompt)
            if session_id:
                log_llm_call(
                    session_id, agent_name, full_prompt, text,
                    usage=usage._asdict() if usage else None,
                    variable_input=variable_input,
                )
            return text
        except Exception as e:
            log_pipeline_event(session_id, "artifact_generation_error", {"error": str(e)})
            if isinstance(e, CostLimitExceeded):
                raise
            return ""

    def _generate_design_doc(
        self,
        node: dict,
        dependency_context: dict[str, str],
        session_id: str,
        causal_path: list[dict] | None = None,
        causal_parent_context: dict[str, str] | None = None,
        interface_definitions: list[dict] | None = None,
    ) -> str:
        """
        Generate canonical design spec (JSON) for a DTG design node.
        Output is for LLM consumption (code/test generation). CVP: causal_path and causal_parent_context.
        """
        prompt = self._load_prompt("design_doc_generator")
        if not prompt:
            return ""

        input_data = {
            "dtg_node": {k: v for k, v in node.items()},
            "dependency_context": dependency_context,
        }
        if causal_path:
            input_data["causal_path"] = causal_path
        if causal_parent_context:
            input_data["causal_parent_context"] = causal_parent_context
        if interface_definitions:
            input_data["interface_definitions"] = interface_definitions
        raw = self._call_llm(prompt, input_data, session_id).strip()
        # Parse and re-serialize to ensure valid JSON; fallback to raw if parse fails
        try:
            from core.json_parser import parse_llm_json
            parsed = parse_llm_json(raw)
            if isinstance(parsed, dict) and parsed.get("type") == "design_spec":
                return json.dumps(parsed, indent=2, default=str)
        except Exception:
            pass
        return raw

    def _generate_code(
        self,
        node: dict,
        framework: str,
        dependency_context: dict[str, str],
        session_id: str,
        causal_path: list[dict] | None = None,
        causal_parent_context: dict[str, str] | None = None,
        interface_definitions: list[dict] | None = None,
        compile_errors: str | None = None,
        design_docs_available: bool = True,
        implementation_brief: str | None = None,
    ) -> dict[str, str]:
        """
        Generate code files for a DTG code node. Returns {path: content}.
        CVP: causal_path and causal_parent_context restrict/annotate context (Markov blanket).
        When compile_errors is set, the model should fix the code to resolve the build output.
        implementation_brief: full design-based prompt text for clarity; use when provided.
        """
        prompt = self._load_prompt("code_generator")
        if not prompt:
            return {}

        input_data = {
            "dtg_node": {k: v for k, v in node.items()},
            "framework": framework,
            "dependency_context": dependency_context,
            "design_docs_available": design_docs_available,
        }
        if causal_path:
            input_data["causal_path"] = causal_path
        if causal_parent_context:
            input_data["causal_parent_context"] = causal_parent_context
        if interface_definitions:
            input_data["interface_definitions"] = interface_definitions
        if compile_errors:
            input_data["compile_errors"] = compile_errors
        if implementation_brief:
            input_data["implementation_brief"] = implementation_brief
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
        nodes_by_id = {n["id"]: n for n in order if n.get("id")}
        resolved: dict[str, str] = {}
        generated_design: list[str] = []

        # Scaffold project structure so it's buildable
        self._scaffold_project(hlig_dir, framework, hlig_node)

        # Design nodes run once
        for node in order:
            nid = node.get("id", "")
            task_type = (node.get("task_type") or "").lower()
            if task_type not in ("design", "documentation"):
                continue
            deps = node.get("dependencies") or []
            dep_ctx = _build_dep_ctx(deps, resolved, nodes_by_id)
            iface_defs = _get_interfaces_for_hlig(hlig_graph, hlig_id) if hlig_graph else None
            doc = self._generate_design_doc(
                node, dep_ctx, session_id, causal_path=causal_path, causal_parent_context=causal_parent_context,
                interface_definitions=iface_defs,
            )
            if doc:
                safe_name = _safe_filename(node.get("title", nid))
                fp = designs_dir / f"{nid}_{safe_name}.json"
                fp.write_text(doc, encoding="utf-8")
                resolved[nid] = _truncate_design(doc)
                generated_design.append(f"designs/{fp.name}")
            log_pipeline_event(session_id, "design_generated", {"node": nid})

        enable_local_build = os.environ.get("ENABLE_LOCAL_BUILD", "1").strip().lower() not in ("0", "false", "no")
        max_build_retries = 1
        compile_errors = None
        for build_attempt in range(max_build_retries + 1):
            generated_code = []
            for node in order:
                nid = node.get("id", "")
                task_type = (node.get("task_type") or "").lower()
                if task_type not in ("code", "integration", "test", "build", "verification"):
                    continue
                deps = node.get("dependencies") or []
                dep_ctx = _build_dep_ctx(deps, resolved, nodes_by_id)
                iface_defs = _get_interfaces_for_hlig(hlig_graph, hlig_id) if hlig_graph else None
                impl_brief = _build_implementation_brief(dep_ctx, iface_defs, node, framework)
                files = self._generate_code(
                    node, framework, dep_ctx, session_id, causal_path=causal_path, causal_parent_context=causal_parent_context,
                    interface_definitions=iface_defs,
                    compile_errors=compile_errors,
                    design_docs_available=True,
                    implementation_brief=impl_brief,
                )
                for rel_path, content in files.items():
                    full_path = hlig_dir / rel_path
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(content, encoding="utf-8")
                    generated_code.append(rel_path)
                if files:
                    resolved[nid] = _make_code_output_spec(nid, list(files.items()))
                    log_pipeline_event(session_id, "code_generated", {"node": nid, "files": list(files.keys())})
            self._scaffold_project(hlig_dir, framework, hlig_node)
            self._write_readme(hlig_dir, hlig_node, framework, generated_design + generated_code)
            if not enable_local_build:
                break
            success, out, err = _run_local_build(hlig_dir, framework)
            _write_build_log(hlig_dir, success, out, err)
            if success:
                log_pipeline_event(session_id, "local_build_ok", {"hlig": hlig_id})
                break
            compile_errors = f"Previous build failed.\nstdout:\n{out}\nstderr:\n{err}"
            log_pipeline_event(session_id, "local_build_retry", {"hlig": hlig_id, "attempt": build_attempt + 1, "stderr_preview": (err or out)[:500]})
        return hlig_dir

    def _load_existing_design_docs(self, designs_dir: Path) -> dict[str, str]:
        """Load design spec content from designs_dir for dependency context. Returns {node_id: content}.
        Loads .json (canonical design_spec) first; falls back to .md for backward compatibility."""
        resolved: dict[str, str] = {}
        if not designs_dir.exists():
            return resolved
        # Prefer .json (canonical design_spec for LLM consumption)
        for fp in designs_dir.glob("*.json"):
            stem = fp.stem
            if "_" in stem:
                nid = stem.split("_", 1)[0]
            else:
                nid = stem
            try:
                resolved[nid] = _truncate_design(fp.read_text(encoding="utf-8"))
            except Exception:
                pass
        # Fallback: legacy .md (for backward compatibility)
        for fp in designs_dir.glob("*.md"):
            stem = fp.stem
            if "_" in stem:
                nid = stem.split("_", 1)[0]
            else:
                nid = stem
            if nid not in resolved:
                try:
                    resolved[nid] = _truncate_design(fp.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return resolved

    def generate_design_docs_only(
        self,
        hlig_graph: HLIGGraph,
        session_id: str,
        date_dir: Path,
    ) -> Path | None:
        """
        Generate only design documents for design-type DTG nodes.
        Creates outputs_{session_id}/ under date_dir. Returns the outputs directory path.
        """
        outputs_dir = date_dir / f"outputs_{session_id}"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        _write_interface_definitions(hlig_graph, outputs_dir)
        topo_order = hlig_graph.topological_order()
        node_data_by_id = {nid: dict(data) for nid, data in hlig_graph.nodes()}
        hlig_outputs: dict[str, str] = {}

        for nid in topo_order:
            data = node_data_by_id.get(nid, {})
            dtg = data.get("dtg")
            if not isinstance(dtg, DTGGraph):
                continue
            hlig_node = {"id": nid, **{k: v for k, v in data.items() if k != "dtg" and not callable(v)}}
            causal_parent_ids = hlig_graph.get_causal_parents(nid)
            causal_parent_context = {pid: hlig_outputs.get(pid, "") for pid in causal_parent_ids if pid in hlig_outputs}

            try:
                hlig_dir = outputs_dir / nid
                hlig_dir.mkdir(parents=True, exist_ok=True)
                designs_dir = hlig_dir / "designs"
                designs_dir.mkdir(exist_ok=True)
                causal_path: list[dict] = []
                if hasattr(hlig_graph, "get_causal_path"):
                    path_tuples = hlig_graph.get_causal_path(nid)
                    causal_path = [
                        {"id": nid2, "task": d.get("task", ""), "outputs": d.get("outputs", [])}
                        for nid2, d in path_tuples
                    ]
                    self._write_causal_path_metadata(hlig_dir, causal_path)
                order = _topological_order(dtg)
                resolved: dict[str, str] = {}
                for node in order:
                    task_type = (node.get("task_type") or "").lower()
                    if task_type not in ("design", "documentation"):
                        continue
                    nid2 = node.get("id", "")
                    deps = node.get("dependencies") or []
                    dep_ctx = {d: resolved.get(d, "") for d in deps if resolved.get(d)}
                    doc = self._generate_design_doc(
                        node, dep_ctx, session_id,
                        causal_path=causal_path,
                        causal_parent_context=causal_parent_context if causal_parent_context else None,
                        interface_definitions=_get_interfaces_for_hlig(hlig_graph, nid),
                    )
                    if doc:
                        safe_name = _safe_filename(node.get("title", nid2))
                        fp = designs_dir / f"{nid2}_{safe_name}.json"
                        fp.write_text(doc, encoding="utf-8")
                        resolved[nid2] = _truncate_design(doc)
                    log_pipeline_event(session_id, "design_generated", {"node": nid2})
                task = hlig_node.get("task", "")
                hlig_outputs[nid] = f"[{nid}] {task}\n(design docs generated)"
            except Exception as e:
                log_pipeline_event(session_id, "artifact_generation_error", {"hlig": nid, "error": str(e)})
                if isinstance(e, CostLimitExceeded):
                    raise

        return outputs_dir

    def generate_code_only(
        self,
        hlig_graph: HLIGGraph,
        session_id: str,
        outputs_dir: Path,
        has_design_docs: bool = True,
    ) -> Path | None:
        """
        Generate only code for code-type DTG nodes.
        When has_design_docs is False (e.g. hlig_no_design_docs pipeline), does not load from designs/;
        dependency context uses dtg_node_ref from DTG metadata only. When True, loads design specs from designs/.
        """
        topo_order = hlig_graph.topological_order()
        node_data_by_id = {nid: dict(data) for nid, data in hlig_graph.nodes()}
        hlig_outputs: dict[str, str] = {}

        for nid in topo_order:
            data = node_data_by_id.get(nid, {})
            dtg = data.get("dtg")
            if not isinstance(dtg, DTGGraph):
                continue
            hlig_node = {"id": nid, **{k: v for k, v in data.items() if k != "dtg" and not callable(v)}}
            hlig_dir = outputs_dir / nid
            causal_parent_ids = hlig_graph.get_causal_parents(nid)
            causal_parent_context = {pid: hlig_outputs.get(pid, "") for pid in causal_parent_ids if pid in hlig_outputs}

            try:
                framework = _infer_framework(hlig_node)
                designs_dir = hlig_dir / "designs"
                if has_design_docs and designs_dir.exists():
                    resolved = self._load_existing_design_docs(designs_dir)
                else:
                    resolved = {}
                order = _topological_order(dtg)
                nodes_by_id = {n["id"]: n for n in order if n.get("id")}
                enable_local_build = os.environ.get("ENABLE_LOCAL_BUILD", "1").strip().lower() not in ("0", "false", "no")
                max_build_retries = 1
                compile_errors = None
                for build_attempt in range(max_build_retries + 1):
                    generated = []
                    for node in order:
                        task_type = (node.get("task_type") or "").lower()
                        if task_type not in ("code", "integration", "test", "build", "verification"):
                            continue
                        nid2 = node.get("id", "")
                        deps = node.get("dependencies") or []
                        dep_ctx = _build_dep_ctx(deps, resolved, nodes_by_id)
                        iface_defs = _get_interfaces_for_hlig(hlig_graph, nid)
                        impl_brief = _build_implementation_brief(dep_ctx, iface_defs, node, framework)
                        causal_path = []
                        if hasattr(hlig_graph, "get_causal_path"):
                            path_tuples = hlig_graph.get_causal_path(nid)
                            causal_path = [
                                {"id": nid3, "task": d.get("task", ""), "outputs": d.get("outputs", [])}
                                for nid3, d in path_tuples
                            ]
                        files = self._generate_code(
                            node, framework, dep_ctx, session_id,
                            causal_path=causal_path,
                            causal_parent_context=causal_parent_context if causal_parent_context else None,
                            interface_definitions=iface_defs,
                            compile_errors=compile_errors,
                            design_docs_available=has_design_docs,
                            implementation_brief=impl_brief,
                        )
                        for rel_path, content in files.items():
                            full_path = hlig_dir / rel_path
                            full_path.parent.mkdir(parents=True, exist_ok=True)
                            full_path.write_text(content, encoding="utf-8")
                            generated.append(rel_path)
                        if files:
                            resolved[nid2] = _make_code_output_spec(nid2, list(files.items()))
                            log_pipeline_event(session_id, "code_generated", {"node": nid2, "files": list(files.keys())})
                    self._scaffold_project(hlig_dir, framework, hlig_node)
                    existing = [f.name for f in ((list(designs_dir.glob("*.json")) + list(designs_dir.glob("*.md"))) if designs_dir.exists() else [])]
                    self._write_readme(hlig_dir, hlig_node, framework, generated + [f"designs/{x}" for x in existing])
                    if not enable_local_build:
                        break
                    success, out, err = _run_local_build(hlig_dir, framework)
                    _write_build_log(hlig_dir, success, out, err)
                    if success:
                        log_pipeline_event(session_id, "local_build_ok", {"hlig": nid})
                        break
                    compile_errors = f"Previous build failed.\nstdout:\n{out}\nstderr:\n{err}"
                    log_pipeline_event(session_id, "local_build_retry", {"hlig": nid, "attempt": build_attempt + 1, "stderr_preview": (err or out)[:500]})
                task = hlig_node.get("task", "")
                readme = hlig_dir / "README.md"
                summary = readme.read_text(encoding="utf-8")[:3000] if readme.exists() else ""
                hlig_outputs[nid] = f"[{nid}] {task}\n{summary}"
            except Exception as e:
                log_pipeline_event(session_id, "artifact_generation_error", {"hlig": nid, "error": str(e)})
                if isinstance(e, CostLimitExceeded):
                    raise

        return outputs_dir

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
        _write_interface_definitions(hlig_graph, outputs_dir)

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
                if isinstance(e, CostLimitExceeded):
                    raise

        return outputs_dir
