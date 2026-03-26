"""Config-driven agent runner using a Plan Graph from the Planner."""

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from context.execution_context import ExecutionContext
from agents.base import BaseAgent
from core.plan_graph import PlanGraph
from core.expansion_engine import EXPANSION_STRATEGIES, default_expansion_strategy_for_node
from core.hlig_dtg_graphs import HLIGGraph, DTGGraph, GraphCycleError

try:
    from core.debug_logger import (
        log_pipeline_event,
        log_user_input,
        get_session_usage,
        log_token_summary,
        CostLimitExceeded,
        log_graph_execution_trace,
        sanitize_plan_dict_for_trace,
    )
except ImportError:
    log_pipeline_event = log_user_input = lambda *a, **kw: None
    get_session_usage = lambda _: {}
    log_token_summary = lambda *a, **kw: None
    CostLimitExceeded = Exception  # noqa: type for isinstance check
    log_graph_execution_trace = lambda *a, **kw: None
    sanitize_plan_dict_for_trace = lambda x: {}

try:
    from core.dtg_artifact_generator import LocalBuildFailedError
except ImportError:
    class LocalBuildFailedError(RuntimeError):
        """Stub when artifact generator is unavailable."""

        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_BASE = PROJECT_ROOT / "session_log" / "sessions"


def _is_clarification_request(output: Any) -> bool:
    """Check if agent output requires user clarification."""
    if not isinstance(output, dict):
        return False
    return "clarificationMessage" in output


def _get_event_bus():
    try:
        from core.event_bus import event_bus
        return event_bus
    except ImportError:
        return None


def _default_plan_from_config(steps: list[dict], pipeline: list[str]) -> dict:
    """Build PlanGraph from pipeline config. Planner produces HLIG only; execution plan comes from config."""
    nodes = []
    edges = []
    prev = PlanGraph.ROOT
    for i, step_name in enumerate(pipeline):
        nid = f"T{i:03d}"
        spec = next((s for s in steps if s.get("name") == step_name), {})
        read_key = "original_query" if i == 0 else f"T{i-1:03d}_output"
        nodes.append({
            "id": nid,
            "agent": step_name,
            "description": spec.get("description", step_name),
            "reads": [read_key],
            "writes": [f"{nid}_output"],
        })
        edges.append({"source": prev, "target": nid})
        prev = nid
    return {"nodes": nodes, "edges": edges}


class AgentRunner:
    """Runs agents based on a Plan Graph. Plan is built from config; Planner produces HLIG only."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        multi_mcp=None,
    ):
        default_config = Path(__file__).parent / "config" / "agents.yaml"
        self.config_path = Path(config_path) if config_path else default_config
        self._config: dict[str, Any] | None = None
        self._multi_mcp = multi_mcp

    @property
    def config(self) -> dict[str, Any]:
        if self._config is None:
            self._config = self._load_config()
        return self._config

    def _load_config(self) -> dict[str, Any]:
        import yaml
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config not found: {self.config_path}")
        with open(self.config_path) as f:
            return yaml.safe_load(f) or {}

    def _run_dtg_generator(self, hlig_node: dict, steps: dict, ctx: ExecutionContext) -> dict | None:
        """Run DTG generator (designer) for one HLIG node. Returns parsed DTG dict or None."""
        spec = steps.get("designer")
        if not spec:
            return None
        prompt_file = spec.get("prompt_file")
        if prompt_file:
            prompt_file = PROJECT_ROOT / prompt_file
        agent = BaseAgent(
            name="designer",
            prompt_file=prompt_file,
            prompt=spec.get("prompt"),
            config=spec.get("config", {}),
            multi_mcp=self._multi_mcp,
            reads=[],
            writes=[],
        )
        agent.run(ctx, input_override=hlig_node)
        artifact = ctx.get_artifact("designer", {})
        if isinstance(artifact, dict):
            out = artifact.get("output")
            if isinstance(out, dict) and "nodes" in out:
                return out
        return None

    @staticmethod
    def _enrich_dtg_nodes(dtg_out: dict, hlig_node: dict) -> dict:
        """
        Enrich each DTG node with parent_hlig and language so nodes are self-contained
        for independent agent execution (design docs, code generation).
        """
        if not dtg_out or not isinstance(dtg_out, dict) or "nodes" not in dtg_out:
            return dtg_out
        parent_hlig = {
            "id": hlig_node.get("id", ""),
            "task": hlig_node.get("task"),
            "inputs": hlig_node.get("inputs", []),
            "outputs": hlig_node.get("outputs", []),
            "language": hlig_node.get("language", "Rust, Tauri, React, CSS"),
            "external_interfaces": hlig_node.get("external_interfaces", []),
        }
        lang = hlig_node.get("language", "Rust, Tauri, React, CSS")
        for n in dtg_out["nodes"]:
            if isinstance(n, dict):
                n["parent_hlig"] = parent_hlig
                n["language"] = lang
                tt = (n.get("task_type") or "").lower()
                if tt in ("code", "integration", "test", "build", "verification"):
                    es = (n.get("expansion_strategy") or "").strip()
                    if not es or es not in EXPANSION_STRATEGIES:
                        n["expansion_strategy"] = default_expansion_strategy_for_node(parent_hlig, n)
        return dtg_out

    def _generate_dtgs_for_hlig(self, ctx: ExecutionContext, hlig_graph: HLIGGraph, steps: dict) -> None:
        """Traverse HLIG nodes, generate DTG for each, attach to node. Retries designer once on DTG cycle."""
        designer_config = (steps.get("designer") or {}).get("config") or {}
        for nid, data in list(hlig_graph.nodes()):
            node_dict = {"id": nid, **{k: v for k, v in data.items() if k != "dtg" and not callable(v)}}
            node_dict.update(designer_config)  # Inject max_design_nodes, max_code_nodes for coarser DTG
            log_pipeline_event(ctx.session_id, "dtg_generation_started", {"hlig_node": nid})
            dtg_out = self._run_dtg_generator(node_dict, steps, ctx)
            if dtg_out and isinstance(dtg_out, dict):
                dtg_out = self._enrich_dtg_nodes(dtg_out, node_dict)
                try:
                    dtg = DTGGraph.from_dict(dtg_out)
                    hlig_graph.set_node_dtg(nid, dtg)
                    log_pipeline_event(ctx.session_id, "dtg_generation_completed", {"hlig_node": nid, "dtg_nodes": len(dtg_out.get("nodes", []))})
                except GraphCycleError as e:
                    log_pipeline_event(
                        ctx.session_id,
                        "graph_cycle_detected",
                        {"graph_type": "DTG", "hlig_node": nid, "cycle_edges": e.cycle_edges, "retry": 1},
                    )
                    retry_dict = {
                        **node_dict,
                        "dtg_cycle_retry_hint": str(e),
                        "dtg_cycle_edges": e.cycle_edges,
                    }
                    dtg_out_retry = self._run_dtg_generator(retry_dict, steps, ctx)
                    if dtg_out_retry and isinstance(dtg_out_retry, dict):
                        dtg_out_retry = self._enrich_dtg_nodes(dtg_out_retry, node_dict)
                        try:
                            dtg = DTGGraph.from_dict(dtg_out_retry)
                            hlig_graph.set_node_dtg(nid, dtg)
                            log_pipeline_event(ctx.session_id, "dtg_generation_completed", {"hlig_node": nid, "dtg_nodes": len(dtg_out_retry.get("nodes", [])), "after_retry": True})
                        except GraphCycleError as e2:
                            log_pipeline_event(ctx.session_id, "dtg_generation_skipped", {"hlig_node": nid, "reason": "cycle_after_retry", "error": str(e2)})
                    else:
                        log_pipeline_event(ctx.session_id, "dtg_generation_skipped", {"hlig_node": nid, "reason": "no valid output after cycle retry"})
            else:
                log_pipeline_event(ctx.session_id, "dtg_generation_skipped", {"hlig_node": nid, "reason": "no valid output"})

    def _run_planner(self, ctx: ExecutionContext, steps: dict, base_agent) -> dict | None:
        """Run Planner agent. Returns full output (may have clarification_needed, questions, or hlig)."""
        spec = steps.get("planner")
        if not spec:
            return None
        prompt_file = spec.get("prompt_file")
        if prompt_file:
            prompt_file = PROJECT_ROOT / prompt_file
        agent = BaseAgent(
            name="planner",
            prompt_file=prompt_file,
            prompt=spec.get("prompt"),
            config=spec.get("config", {}),
            multi_mcp=self._multi_mcp,
            reads=["original_query", "user_clarification"],
            writes=["hlig"],
        )
        ctx = agent.run(ctx)
        artifact = ctx.get_artifact("planner", {})
        if not isinstance(artifact, dict):
            return None
        out = artifact.get("output")
        if isinstance(out, dict):
            return out
        return None

    def _run_node(
        self,
        node_id: str,
        node_data: dict,
        ctx: ExecutionContext,
        steps: dict,
        base_agent,
    ) -> ExecutionContext:
        """Execute a single plan node."""
        agent_name = node_data.get("agent", "")
        spec = steps.get(agent_name)
        if not spec:
            raise ValueError(f"Unknown agent '{agent_name}' for node {node_id}")
        prompt_file = spec.get("prompt_file")
        if prompt_file:
            prompt_file = PROJECT_ROOT / prompt_file
        agent = BaseAgent(
            name=agent_name,
            prompt_file=prompt_file,
            config=spec.get("config", {}),
            multi_mcp=self._multi_mcp,
            reads=node_data.get("reads", []),
            writes=node_data.get("writes", []),
        )
        return agent.run(ctx)

    def _provision_dependencies(self, ctx: ExecutionContext, outputs_path: Path) -> None:
        """
        Provision external dependencies (DB, Auth, Storage) before build.
        Generates .env and .env.test with mock/local URLs so build and test can run.
        """
        if not ctx.hlig_graph:
            return
        try:
            from core.provision_dependencies import provision_all
            results = provision_all(
                ctx.hlig_graph,
                outputs_path,
                session_id=ctx.session_id,
                use_docker_compose=False,
            )
            if results:
                log_pipeline_event(ctx.session_id, "dependencies_provisioned", {"provisioned": results})
        except Exception as e:
            log_pipeline_event(ctx.session_id, "provision_error", {"error": str(e)})

    def _trigger_builds_for_hlig(self, ctx: ExecutionContext) -> None:
        """
        Trigger MCP build_dtg_output for each HLIG with generated artifacts.
        Uses Tauri + Node.js sandbox. Runs async via event loop from sync context.
        Sets ctx.globals_schema["build_failed"] = True if any build fails (testers skip).
        """
        if not self._multi_mcp or not ctx.hlig_graph:
            return
        loop = getattr(self, "_loop", None)
        if not loop:
            return
        import asyncio

        build_failed = False
        for nid, data in ctx.hlig_graph.nodes():
            if not data.get("dtg"):
                continue
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._multi_mcp.route_tool_call("build_dtg_output", {
                        "run_id": ctx.session_id,
                        "hlig_id": nid,
                        "framework": "auto",
                    }),
                    loop,
                )
                result = fut.result(timeout=150)
                text = ""
                if hasattr(result, "content") and result.content:
                    text = result.content[0].text if result.content else ""
                else:
                    text = str(result)
                import json
                try:
                    parsed = json.loads(text)
                    status = parsed.get("status", "unknown")
                    if str(status).lower() in ("failure", "failed", "error"):
                        build_failed = True
                    log_pipeline_event(ctx.session_id, "build_triggered", {"hlig_id": nid, "build_status": status})
                    # Persist MCP build output to build.log for debugging
                    outputs_path = ctx.globals_schema.get("artifact_outputs_path")
                    if outputs_path:
                        hlig_dir = Path(outputs_path) / str(nid)
                        if hlig_dir.is_dir():
                            log_path = hlig_dir / "build.log"
                            ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                            block = f"\n{'='*60}\n[{ts}] [MCP build] {status}\n{'='*60}\n"
                            for key in ("stdout", "stderr"):
                                val = parsed.get(key)
                                if val:
                                    block += f"--- {key} ---\n{val}\n"
                            try:
                                with open(log_path, "a", encoding="utf-8") as f:
                                    f.write(block)
                            except Exception:
                                pass
                except json.JSONDecodeError:
                    build_failed = True
                    log_pipeline_event(ctx.session_id, "build_triggered", {"hlig_id": nid, "output": text[:500]})
            except Exception as e:
                build_failed = True
                log_pipeline_event(ctx.session_id, "build_error", {"hlig_id": nid, "error": str(e)})
        if build_failed:
            ctx.globals_schema["build_failed"] = True

    def _run_pipeline_phase(
        self,
        agent_name: str,
        node_id: str,
        node_data: dict,
        ctx: ExecutionContext,
        steps: dict,
    ) -> ExecutionContext:
        """
        Run a pipeline phase that has special logic (designer, coder, builder, etc.).
        Returns updated ctx. For LLM-based phases, runs agent and returns; for
        builder/coder, performs side effects and records artifact.
        """
        if agent_name == "designer":
            if ctx.hlig_graph:
                self._generate_dtgs_for_hlig(ctx, ctx.hlig_graph, steps)
            ctx.add_artifact("designer", {"output": "DTGs attached to HLIG nodes"})
            return ctx

        if agent_name == "design_doc_generator":
            if not ctx.hlig_graph:
                ctx.add_artifact("design_doc_generator", {"output": "No HLIG graph; skipping design docs"})
                return ctx
            try:
                from core.dtg_artifact_generator import DTGArtifactGenerator
                dt = datetime.now()
                date_dir = SESSIONS_BASE / str(dt.year) / f"{dt.month:02d}" / f"{dt.day:02d}"
                date_dir.mkdir(parents=True, exist_ok=True)
                gen = DTGArtifactGenerator()
                outputs_path = gen.generate_design_docs_only(ctx.hlig_graph, ctx.session_id, date_dir)
                if outputs_path:
                    ctx.globals_schema["artifact_outputs_path"] = str(outputs_path)
                    log_pipeline_event(ctx.session_id, "design_docs_generated", {"path": str(outputs_path)})
                ctx.add_artifact("design_doc_generator", {"output": "Design docs generated", "path": str(outputs_path) if outputs_path else None})
            except Exception as e:
                log_pipeline_event(ctx.session_id, "artifact_generation_error", {"error": str(e)})
                ctx.add_artifact("design_doc_generator", {"output": f"Error: {e}", "error": str(e)})
                if isinstance(e, CostLimitExceeded):
                    raise
            return ctx

        if agent_name == "design_reviewer":
            spec = steps.get("design_reviewer", {})
            prompt_file = spec.get("prompt_file")
            input_data = {
                "hlig_graph": ctx.hlig_graph.to_dict() if ctx.hlig_graph and hasattr(ctx.hlig_graph, "to_dict") else {},
                "original_query": ctx.globals_schema.get("original_query", ctx.get_state("query", "")),
                "user_clarification": ctx.globals_schema.get("user_clarification", ""),
                "artifact_outputs_path": ctx.globals_schema.get("artifact_outputs_path", ""),
            }
            agent = BaseAgent(
                name="design_reviewer",
                prompt_file=PROJECT_ROOT / prompt_file if prompt_file else None,
                config=spec.get("config", {}),
                multi_mcp=self._multi_mcp,
                reads=[],
                writes=[],
            )
            return agent.run(ctx, input_override=input_data)

        if agent_name == "coder":
            if not ctx.hlig_graph:
                ctx.add_artifact("coder", {"output": "No HLIG graph; nothing to generate"})
                return ctx
            try:
                from core.dtg_artifact_generator import DTGArtifactGenerator, _write_interface_definitions
                gen = DTGArtifactGenerator()
                outputs_path = None
                pipeline_steps = ctx.globals_schema.get("pipeline_steps", [])
                has_design_docs = "design_doc_generator" in pipeline_steps
                existing_outputs = ctx.globals_schema.get("artifact_outputs_path")
                if existing_outputs and Path(existing_outputs).exists():
                    outputs_path = gen.generate_code_only(
                        ctx.hlig_graph, ctx.session_id, Path(existing_outputs), has_design_docs=has_design_docs
                    )
                else:
                    # No design docs pipeline (e.g. hlig_no_design_docs): create outputs dir, write interfaces, generate code only
                    dt = datetime.now()
                    date_dir = SESSIONS_BASE / str(dt.year) / f"{dt.month:02d}" / f"{dt.day:02d}"
                    date_dir.mkdir(parents=True, exist_ok=True)
                    outputs_dir = date_dir / f"outputs_{ctx.session_id}"
                    outputs_dir.mkdir(parents=True, exist_ok=True)
                    _write_interface_definitions(ctx.hlig_graph, outputs_dir)
                    outputs_path = gen.generate_code_only(
                        ctx.hlig_graph, ctx.session_id, outputs_dir, has_design_docs=has_design_docs
                    )
                if outputs_path:
                    ctx.globals_schema["artifact_outputs_path"] = str(outputs_path)
                    log_pipeline_event(ctx.session_id, "artifact_generation_completed", {"path": str(outputs_path)})
                    self._provision_dependencies(ctx, outputs_path)
                ctx.add_artifact("coder", {"output": "Artifacts generated", "path": str(outputs_path) if outputs_path else None})
            except Exception as e:
                log_pipeline_event(ctx.session_id, "artifact_generation_error", {"error": str(e)})
                ctx.add_artifact("coder", {"output": f"Error: {e}", "error": str(e)})
                if isinstance(e, LocalBuildFailedError):
                    raise
                if isinstance(e, CostLimitExceeded):
                    raise
            return ctx

        if agent_name == "code_reviewer":
            spec = steps.get("code_reviewer", {})
            prompt_file = spec.get("prompt_file")
            input_data = {
                "artifact_outputs_path": ctx.globals_schema.get("artifact_outputs_path", ""),
                "hlig_graph": ctx.hlig_graph.to_dict() if ctx.hlig_graph and hasattr(ctx.hlig_graph, "to_dict") else {},
                "original_query": ctx.globals_schema.get("original_query", ctx.get_state("query", "")),
                "user_clarification": ctx.globals_schema.get("user_clarification", ""),
            }
            agent = BaseAgent(
                name="code_reviewer",
                prompt_file=PROJECT_ROOT / prompt_file if prompt_file else None,
                config=spec.get("config", {}),
                multi_mcp=self._multi_mcp,
                reads=[],
                writes=[],
            )
            return agent.run(ctx, input_override=input_data)

        if agent_name == "builder":
            self._trigger_builds_for_hlig(ctx)
            ctx.add_artifact("builder", {"output": "Builds triggered via MCP"})
            return ctx

        if agent_name in ("unit_tester", "integration_tester", "system_tester"):
            if ctx.globals_schema.get("build_failed"):
                ctx.add_artifact(agent_name, {"output": "Skipped (build failed)", "skipped": True})
                log_pipeline_event(ctx.session_id, "tester_skipped", {"agent": agent_name, "reason": "build_failed"})
                return ctx
            spec = steps.get(agent_name, {})
            prompt_file = spec.get("prompt_file")
            input_data = {
                "hlig_graph": ctx.hlig_graph.to_dict() if ctx.hlig_graph and hasattr(ctx.hlig_graph, "to_dict") else {},
                "artifact_outputs_path": ctx.globals_schema.get("artifact_outputs_path", ""),
                "original_query": ctx.globals_schema.get("original_query", ctx.get_state("query", "")),
            }
            agent = BaseAgent(
                name=agent_name,
                prompt_file=PROJECT_ROOT / prompt_file if prompt_file else None,
                config=spec.get("config", {}),
                multi_mcp=self._multi_mcp,
                reads=[],
                writes=[],
            )
            return agent.run(ctx, input_override=input_data)

        return None

    def _extract_writes(self, ctx: ExecutionContext, node_data: dict, output: Any) -> None:
        """Extract write keys from node output into globals_schema."""
        writes = node_data.get("writes", [])
        if not writes:
            return
        agent_name = node_data.get("agent", "")
        artifact = ctx.get_artifact(agent_name, {}) if agent_name else {}
        # Try exact key match in output
        if isinstance(output, dict):
            for key in writes:
                if key in output:
                    ctx.globals_schema[key] = output[key]
                elif isinstance(output.get("output"), dict) and key in output["output"]:
                    ctx.globals_schema[key] = output["output"][key]
        # Fallback: use artifact or its output for first write key
        if isinstance(artifact, dict):
            for key in writes:
                if key in artifact:
                    ctx.globals_schema[key] = artifact[key]
                elif key not in ctx.globals_schema and "output" in artifact:
                    ctx.globals_schema[key] = artifact["output"]

    def run(
        self,
        ctx: ExecutionContext | None = None,
        pipeline: str | list[str] | None = None,
        event_loop=None,
        on_step_complete=None,
        wait_for_input: Callable[[str, str, list[str] | None, str], str] | None = None,
    ) -> ExecutionContext:
        """
        Run using Plan Graph:
        1. Run Planner to get HLIG (with clarification loop); build plan from config
        2. Execute DAG: get_ready_steps, run each, mark done
        3. Support replanning on failure (optional)
        """
        self._loop = event_loop
        if ctx is None:
            ctx = ExecutionContext.create()

        steps = {s["name"]: s for s in self.config.get("steps", [])}
        pipelines = self.config.get("pipelines", {})
        default_pipeline = self.config.get("default_pipeline", "default")
        step_names = (
            pipelines.get(pipeline, [pipeline])
            if isinstance(pipeline, str)
            else (pipeline or pipelines.get(default_pipeline, []))
        )
        ctx.globals_schema["pipeline_steps"] = list(step_names)

        # Seed globals
        ctx.globals_schema.setdefault("original_query", ctx.get_state("query", ""))

        log_pipeline_event(ctx.session_id, "run_started", {"query": ctx.get_state("query", "")})
        log_graph_execution_trace(
            ctx.session_id,
            "run_started",
            {
                "pipeline_steps": list(step_names),
                "query_preview": str(ctx.get_state("query", ""))[:500],
            },
        )

        # Phase 1: Get plan from Planner (supports clarification loop per prompts/planner.md)
        plan_dict = None
        while True:
            log_pipeline_event(ctx.session_id, "phase1_planner", "running planner")
            planner_out = self._run_planner(ctx, steps, BaseAgent)
            if planner_out is None:
                plan_dict = _default_plan_from_config(
                    list(steps.values()),
                    step_names,
                )
                break
            if planner_out.get("clarification_needed") and planner_out.get("questions"):
                if not wait_for_input:
                    plan_dict = _default_plan_from_config(
                        list(steps.values()),
                        step_names,
                    )
                    break
                questions = planner_out.get("questions", [])
                msg = "\n\n".join(
                    f"{q.get('id', '')}: {q.get('question', '')}".strip().lstrip(":")
                    for q in questions
                ) or "Please provide additional details for your project."
                if on_step_complete:
                    try:
                        on_step_complete(ctx)
                    except Exception:
                        pass
                log_pipeline_event(ctx.session_id, "waiting_user_input", {"node": "planner_clarification", "message": msg[:200]})
                user_response = wait_for_input(
                    "planner_clarification",
                    msg,
                    None,
                    "user_clarification",
                )
                log_user_input(ctx.session_id, "planner_clarification", msg, user_response)
                # Accumulate responses so planner sees full history (avoids repeated questions)
                existing = ctx.globals_schema.get("user_clarification", "")
                ctx.globals_schema["user_clarification"] = (
                    f"{existing}\n\n{user_response}".strip() if existing else user_response
                )
                if on_step_complete:
                    try:
                        on_step_complete(ctx)
                    except Exception:
                        pass
                continue
            if planner_out.get("hlig"):
                try:
                    hlig_graph = HLIGGraph.from_planner_hlig(planner_out)
                    removed_cycles = planner_out.pop("_hlig_cycle_edges_removed", None)
                    if removed_cycles:
                        log_pipeline_event(
                            ctx.session_id,
                            "hlig_cycles_auto_removed",
                            {"removed_edges": removed_cycles},
                        )
                    if hlig_graph:
                        ctx.hlig_graph = hlig_graph
                except GraphCycleError as e:
                    retry_count = ctx.globals_schema.get("hlig_cycle_retry_count", 0)
                    log_pipeline_event(
                        ctx.session_id,
                        "graph_cycle_detected",
                        {"graph_type": "HLIG", "cycle_edges": e.cycle_edges, "retry": retry_count + 1},
                    )
                    if retry_count < 1:
                        ctx.globals_schema["hlig_cycle_retry_count"] = retry_count + 1
                        existing = ctx.globals_schema.get("user_clarification", "")
                        cycle_detail = ""
                        if getattr(e, "cycle_edges", None):
                            cycle_detail = f" Problematic edges (remove at least one or use a single direction): {e.cycle_edges}."
                        ctx.globals_schema["user_clarification"] = (
                            f"{existing}\n\n[System: The previous HLIG graph contained a cycle: {e}.{cycle_detail} "
                            "Edges must form a DAG: do not add both A→B and B→A. Use one directed edge for each dependency "
                            "(e.g. backend → frontend for API consumption).]".strip()
                            if existing
                            else (
                                f"[System: The HLIG graph contained a cycle: {e}.{cycle_detail} "
                                "Edges must form a DAG: do not add both A→B and B→A. Use one directed edge per dependency "
                                "(e.g. backend → frontend for API consumption).]"
                            )
                        )
                        continue
                    raise
            plan_dict = _default_plan_from_config(list(steps.values()), step_names)
            break

        plan = PlanGraph.from_dict(plan_dict)
        ctx.plan_graph = plan
        log_graph_execution_trace(
            ctx.session_id,
            "plan_initialized",
            {
                "has_hlig_graph": ctx.hlig_graph is not None,
                **sanitize_plan_dict_for_trace(plan.to_dict()),
            },
        )

        # Mark planner done when we already ran it (full pipeline with HLIG)
        if ctx.hlig_graph and "designer" in step_names and "design_reviewer" in step_names:
            planner_node_id = next(
                (nid for nid, data in plan.nodes() if data.get("agent") == "planner"),
                None,
            )
            if planner_node_id:
                planner_artifact = ctx.get_artifact("planner", {})
                plan.mark_done(planner_node_id, output=planner_artifact.get("output") if isinstance(planner_artifact, dict) else planner_artifact)

        log_pipeline_event(ctx.session_id, "phase2_dag", {"plan": plan_dict})
        log_graph_execution_trace(ctx.session_id, "phase2_dag_started", {"message": "executing plan graph"})

        # Phase 2: DAG execution
        bus = _get_event_bus()
        try:
            while plan.has_pending():
                ready = plan.get_ready_steps()
                if not ready:
                    break
                for node_id in ready:
                    node_data = plan.get_node(node_id)
                    if not node_data:
                        continue
                    agent_name = node_data.get("agent", "")
                    if agent_name == "System" or not agent_name:
                        plan.mark_done(node_id)
                        log_graph_execution_trace(
                            ctx.session_id,
                            "node_skipped",
                            {"node_id": node_id, "agent": agent_name or "(empty)", "reason": "system_or_no_agent"},
                        )
                        continue

                    plan.mark_running(node_id)
                    log_pipeline_event(ctx.session_id, "node_started", {"node_id": node_id, "agent": agent_name})
                    log_graph_execution_trace(
                        ctx.session_id,
                        "node_started",
                        {
                            "node_id": node_id,
                            "agent": agent_name,
                            "description": str(node_data.get("description", ""))[:300],
                            "reads": node_data.get("reads"),
                            "writes": node_data.get("writes"),
                        },
                    )
                    try:
                        # Record node start in graph_state for observability.
                        ctx.graph_state.record_start(node_id, agent_name, inputs={})
                        phase_ctx = self._run_pipeline_phase(agent_name, node_id, node_data, ctx, steps)
                        if phase_ctx is not None:
                            ctx = phase_ctx
                        else:
                            ctx = self._run_node(node_id, node_data, ctx, steps, BaseAgent)
                        output = ctx.get_artifact(agent_name, {})
                        if isinstance(output, dict) and "output" in output:
                            out_val = output.get("output", output)
                        else:
                            out_val = output
                        if _is_clarification_request(out_val) and wait_for_input:
                            plan.get_node(node_id)["status"] = "waiting_input"
                            log_graph_execution_trace(
                                ctx.session_id,
                                "node_waiting_input",
                                {"node_id": node_id, "agent": agent_name},
                            )
                            if on_step_complete:
                                try:
                                    on_step_complete(ctx)
                                except Exception:
                                    pass
                            write_key = out_val.get("writes_to", "user_clarification")
                            cl_msg = out_val.get("clarificationMessage", "Please provide input.")
                            log_pipeline_event(ctx.session_id, "waiting_user_input", {"node": node_id, "message": cl_msg[:200]})
                            user_response = wait_for_input(
                                node_id,
                                cl_msg,
                                out_val.get("options"),
                                write_key,
                            )
                            log_user_input(ctx.session_id, node_id, cl_msg, user_response)
                            # Accumulate when write_key is user_clarification (avoids repeated questions)
                            if write_key == "user_clarification":
                                existing = ctx.globals_schema.get(write_key, "")
                                ctx.globals_schema[write_key] = (
                                    f"{existing}\n\n{user_response}".strip() if existing else user_response
                                )
                            else:
                                ctx.globals_schema[write_key] = user_response
                            if on_step_complete:
                                try:
                                    on_step_complete(ctx)
                                except Exception:
                                    pass
                            out_val = {"clarificationMessage": out_val.get("clarificationMessage"), "user_response": user_response}
                            plan.mark_done(node_id, output=out_val)
                        else:
                            plan.mark_done(node_id, output=out_val)
                            self._extract_writes(ctx, node_data, out_val)

                        nd = plan.get_node(node_id) or {}
                        log_graph_execution_trace(
                            ctx.session_id,
                            "node_completed",
                            {
                                "node_id": node_id,
                                "agent": agent_name,
                                "status": nd.get("status"),
                                "execution_time_s": nd.get("execution_time"),
                            },
                        )

                        spec = steps.get(agent_name, {})
                        prompt_file = spec.get("prompt_file")
                        agent = BaseAgent(
                            name=agent_name,
                            prompt_file=PROJECT_ROOT / prompt_file if prompt_file else None,
                            config=spec.get("config", {}),
                            multi_mcp=self._multi_mcp,
                        )
                        ctx.record_agent_run(agent_name, agent)
                        log_pipeline_event(ctx.session_id, "node_completed", {"node_id": node_id, "agent": agent_name})
                        # Record successful completion in graph_state.
                        ctx.graph_state.record_end(node_id, outputs={"output": out_val}, error=None)

                        if on_step_complete:
                            try:
                                on_step_complete(ctx)
                            except Exception:
                                pass
                        if bus and self._loop:
                            import asyncio
                            fut = asyncio.run_coroutine_threadsafe(
                                bus.publish("step_completed", ctx.session_id, {
                                    "step": agent_name,
                                    "node_id": node_id,
                                    "session_id": ctx.session_id,
                                    "artifacts": dict(ctx.artifacts),
                                }),
                                self._loop,
                            )
                            try:
                                fut.result(timeout=2)
                            except Exception:
                                pass
                    except Exception as e:
                        plan.mark_failed(node_id, error=str(e))
                        # Record failure in graph_state before bubbling up.
                        ctx.graph_state.record_end(node_id, outputs={}, error=str(e))
                        fd = plan.get_node(node_id) or {}
                        log_graph_execution_trace(
                            ctx.session_id,
                            "node_failed",
                            {
                                "node_id": node_id,
                                "agent": agent_name,
                                "error_type": type(e).__name__,
                                "error": str(e)[:2000],
                                "status": fd.get("status"),
                            },
                        )
                        raise
        except CostLimitExceeded:
            # Log final cost and run_completed before re-raising so logs show why run stopped
            totals = get_session_usage(ctx.session_id)
            log_graph_execution_trace(
                ctx.session_id,
                "run_stopped",
                {
                    "reason": "cost_limit_exceeded",
                    "total_cost_usd": totals.get("cost_usd", 0) if totals else 0,
                    "total_tokens": totals.get("total_tokens", 0) if totals else 0,
                },
            )
            if totals:
                log_token_summary(ctx.session_id, totals)
                log_pipeline_event(
                    ctx.session_id,
                    "run_completed",
                    {
                        "total_cost_usd": totals.get("cost_usd", 0),
                        "total_tokens": totals.get("total_tokens", 0),
                        "stopped": "cost_limit_exceeded",
                    },
                )
            raise

        # Log session-level token/cost summary and run completed with total cost
        totals = get_session_usage(ctx.session_id)
        _final_nodes: list[dict] = []
        for nid, data in plan.nodes():
            if nid == PlanGraph.ROOT:
                continue
            _final_nodes.append(
                {
                    "node_id": nid,
                    "agent": data.get("agent"),
                    "status": data.get("status"),
                    "execution_time_s": data.get("execution_time"),
                    "error": (str(data.get("error"))[:500] if data.get("error") else None),
                }
            )
        log_graph_execution_trace(
            ctx.session_id,
            "run_finished",
            {
                "all_done": plan.all_done(),
                "nodes": _final_nodes,
                "total_cost_usd": totals.get("cost_usd", 0) if totals else 0,
                "total_tokens": totals.get("total_tokens", 0) if totals else 0,
            },
        )
        if totals:
            log_token_summary(ctx.session_id, totals)
            log_pipeline_event(
                ctx.session_id,
                "run_completed",
                {"total_cost_usd": totals.get("cost_usd", 0), "total_tokens": totals.get("total_tokens", 0)},
            )

        return ctx

    def _replan(
        self,
        ctx: ExecutionContext,
        failed_node_id: str,
        error: str,
        current_plan: PlanGraph,
        steps: dict,
    ) -> PlanGraph | None:
        """
        Call Planner with failure context; return new plan or None.
        Override or extend for custom replanning logic.
        """
        # Build completed/failed info for Planner
        completed = [
            nid for nid, data in current_plan.nodes()
            if data.get("status") == "completed"
        ]
        failed = [failed_node_id]
        # TODO: Run Planner with completed_steps, failed_steps; parse new plan_graph
        return None
