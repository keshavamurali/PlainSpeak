"""Runs router - create runs, list, get, stream."""

import asyncio
import copy
import threading
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from shared.state import get_multi_mcp, active_sessions

import sys

try:
    from core.debug_logger import log_pipeline_event, CostLimitExceeded
except ImportError:
    log_pipeline_event = lambda *a, **kw: None
    CostLimitExceeded = Exception  # noqa: type to satisfy isinstance
from agents.runner import AgentRunner
from core.expansion_engine import EXPANSION_STRATEGIES, default_expansion_strategy_for_node
from context.execution_context import ExecutionContext
from session.manager import SessionManager
from core.event_bus import event_bus

router = APIRouter(tags=["Runs"])
session_manager = SessionManager()


class RunRequest(BaseModel):
    query: str
    pipeline: str | None = None  # e.g. "hlig_full", "hlig_no_design_docs" - uses default if omitted


class InputRequest(BaseModel):
    response: str


class RunResponse(BaseModel):
    id: str
    status: str
    created_at: str
    query: str


async def process_run(run_id: str, query: str, pipeline: str | None = None) -> None:
    """Background task: run agent pipeline and publish events."""
    loop = asyncio.get_running_loop()
    mcp = get_multi_mcp()
    runner = AgentRunner(multi_mcp=mcp)
    ctx = ExecutionContext.create(session_id=run_id, query=query)
    ctx.set_state("query", query)
    ctx.globals_schema["original_query"] = query

    input_event = threading.Event()
    input_value: list[str] = []  # mutable to hold response

    def wait_for_input(node_id: str, message: str, options: list[str] | None, write_key: str) -> str:
        active_sessions[run_id]["pending_clarification"] = {
            "node_id": node_id,
            "message": message,
            "options": options,
            "write_key": write_key,
        }
        d = ctx.to_dict()
        d["status"] = "running"
        d["query"] = query
        d["pending_clarification"] = active_sessions[run_id]["pending_clarification"]
        session_manager.save_session(run_id, d)
        asyncio.run_coroutine_threadsafe(
            event_bus.publish("clarification", run_id, {"run_id": run_id, "message": message, "options": options}),
            loop,
        ).result(timeout=2)
        input_value.clear()
        input_event.clear()
        input_event.wait()
        active_sessions[run_id]["pending_clarification"] = None
        return input_value[0] if input_value else ""

    try:
        active_sessions[run_id] = {
            "status": "running",
            "query": query,
            "input_event": input_event,
            "input_value": input_value,
            "pending_clarification": None,
        }
        await event_bus.publish("run_started", run_id, {"run_id": run_id, "query": query})

        def save_progress(ctx):
            d = ctx.to_dict()
            d["status"] = "running"
            d["query"] = query
            d["pending_clarification"] = active_sessions.get(run_id, {}).get("pending_clarification")
            session_manager.save_session(run_id, d)

        ctx = await asyncio.to_thread(
            runner.run,
            ctx=ctx,
            pipeline=pipeline,
            event_loop=loop,
            on_step_complete=save_progress,
            wait_for_input=wait_for_input,
        )

        log_pipeline_event(run_id, "run_completed", {"status": "completed"})
        await event_bus.publish("run_completed", run_id, {"run_id": run_id})
        data = ctx.to_dict()
        data["status"] = "completed"
        data["query"] = query
        session_manager.save_session(run_id, data)

    except Exception as e:
        err_msg = str(e)
        log_pipeline_event(run_id, "run_failed", {"error": err_msg})
        await event_bus.publish("run_failed", run_id, {"run_id": run_id, "error": err_msg})
        data = ctx.to_dict()
        data["status"] = "failed"
        data["query"] = query
        data["error"] = err_msg
        session_manager.save_session(run_id, data)
        # Show error on command line when running server
        print(f"\n[PlainSpeak] Run {run_id} FAILED: {err_msg}\n", file=sys.stderr, flush=True)
    finally:
        if run_id in active_sessions:
            del active_sessions[run_id]


def _planner_questions_to_message(planner_out: dict) -> str | None:
    """Format planner clarification questions for chat display."""
    if not planner_out.get("clarification_needed") or not planner_out.get("questions"):
        return None
    parts = []
    for q in planner_out.get("questions", []):
        qid = q.get("id", "")
        qtext = q.get("question", "")
        if qid and qtext:
            parts.append(f"{qid}: {qtext}")
        elif qtext:
            parts.append(qtext)
    return "\n\n".join(parts) if parts else None


def _build_messages(data: dict) -> list[dict]:
    """Build full chat history from run data for display (user/assistant turns in order)."""
    messages = []
    query = data.get("query", data.get("state", {}).get("query", ""))
    if query:
        messages.append({"role": "user", "content": query})
    runs_list = data.get("runs", [])
    artifacts = data.get("artifacts", {})
    pending = data.get("pending_clarification")
    globals_ = data.get("globals_schema", {})

    planner_art = artifacts.get("planner", {})
    planner_out = planner_art.get("output", {}) if isinstance(planner_art, dict) else {}
    planner_questions_msg = _planner_questions_to_message(planner_out) if isinstance(planner_out, dict) else None
    user_clarification = globals_.get("user_clarification", "")
    has_clarification_agent = any(r.get("agent") == "clarification" for r in runs_list)

    # Planner phase (runs before DAG, so never in run_history)
    if planner_questions_msg:
        messages.append({
            "role": "assistant",
            "content": planner_questions_msg,
            "clarification": True,
            "options": None,
        })
        # Add user response only if clarification agent didn't overwrite it (no later clarification round)
        if user_clarification and not has_clarification_agent:
            messages.append({"role": "user", "content": user_clarification})
    elif user_clarification and not has_clarification_agent:
        # Planner artifact was overwritten when it re-ran after user response (we lost the questions),
        # but user_clarification is still in globals - show the user's answer so it appears in chat.
        messages.append({"role": "user", "content": user_clarification})

    # DAG agents (clarification, coder, reviewer)
    for r in runs_list:
        agent = r.get("agent", "")
        if agent == "planner":
            if not planner_questions_msg:
                messages.append({"role": "assistant", "content": "I've analyzed your request.", "step": agent})
        elif agent == "clarification":
            cl_art = artifacts.get("clarification", {})
            out = cl_art.get("output", {}) if isinstance(cl_art, dict) else {}
            msg = out.get("clarificationMessage") or (pending.get("message") if pending else "Please confirm.")
            messages.append({
                "role": "assistant",
                "content": msg,
                "clarification": True,
                "options": out.get("options") or (pending.get("options") if pending else None),
            })
            if user_clarification:
                messages.append({"role": "user", "content": user_clarification})
        elif agent not in ("clarification",):
            raw = str(artifacts.get(agent, {}).get("output", ""))
            content = raw[:2000] + ("..." if len(raw) > 2000 else "")
            messages.append({"role": "assistant", "content": content or f"Completed {agent}", "step": agent})

    # Current pending clarification (waiting for user)
    if pending and not any(m.get("clarification") for m in messages):
        msg = pending.get("message") or planner_questions_msg or "Please provide your response."
        messages.append({
            "role": "assistant",
            "content": msg,
            "clarification": True,
            "options": pending.get("options"),
        })
    elif planner_questions_msg and not any(m.get("clarification") for m in messages) and not user_clarification:
        messages.append({
            "role": "assistant",
            "content": planner_questions_msg,
            "clarification": True,
            "options": None,
        })
    return messages


def _compute_causal_paths(hlig_graph: dict) -> dict:
    """
    CVP: Compute causal path for each HLIG node from graph structure.
    Returns {node_id: [{id, task, outputs}, ...]} for frontend display.
    """
    if not hlig_graph or not isinstance(hlig_graph, dict):
        return {}
    try:
        from core.hlig_dtg_graphs import HLIGGraph
        nodes = hlig_graph.get("nodes", [])
        edges = hlig_graph.get("edges", [])
        if not nodes:
            return {}
        # Rebuild HLIGGraph from stored dict; edges may use from/to or source/target
        g = HLIGGraph(nodes=nodes, edges=edges)
        result = {}
        for nid, _ in g.nodes():
            path_tuples = g.get_causal_path(nid)
            result[nid] = [
                {"id": pid, "task": d.get("task", ""), "outputs": d.get("outputs", [])}
                for pid, d in path_tuples
            ]
        return result
    except Exception:
        return {}


def _enrich_hlig_graph(hlig_graph: dict) -> dict:
    """
    Enrich DTG nodes with parent_hlig and language when missing (e.g. from older sessions).
    Makes each DTG node self-contained for independent agent execution.
    """
    if not hlig_graph or not isinstance(hlig_graph, dict):
        return hlig_graph
    nodes = hlig_graph.get("nodes", [])
    for node in nodes:
        if not isinstance(node, dict):
            continue
        dtg = node.get("dtg")
        if not isinstance(dtg, dict) or "nodes" not in dtg:
            continue
        parent_hlig = {
            "id": node.get("id", ""),
            "task": node.get("task"),
            "inputs": node.get("inputs", []),
            "outputs": node.get("outputs", []),
            "language": node.get("language", "Rust, Tauri, React, CSS"),
            "external_interfaces": node.get("external_interfaces", []),
        }
        lang = node.get("language", "Rust, Tauri, React, CSS")
        for n in dtg.get("nodes", []):
            if isinstance(n, dict) and "parent_hlig" not in n:
                n["parent_hlig"] = parent_hlig
                n["language"] = lang
            if isinstance(n, dict):
                tt = (n.get("task_type") or "").lower()
                if tt in ("code", "integration", "test", "build", "verification", "scaffold"):
                    es = (n.get("expansion_strategy") or "").strip()
                    if not es or es not in EXPANSION_STRATEGIES:
                        n["expansion_strategy"] = default_expansion_strategy_for_node(parent_hlig, n)
    return hlig_graph


def _run_to_response(data: dict) -> dict:
    """Format run/session data for API response."""
    run_id = data.get("session_id", data.get("id", ""))
    status = data.get("status", "unknown")
    if data.get("pending_clarification"):
        status = "waiting_input"
    result = {
        "id": run_id,
        "status": status,
        "created_at": data.get("created_at", ""),
        "query": data.get("query", data.get("state", {}).get("query", "")),
        "artifacts": data.get("artifacts", {}),
        "runs": data.get("runs", []),
        "plan_graph": data.get("plan_graph"),
        "pending_clarification": data.get("pending_clarification"),
        "messages": _build_messages(data),
        "error": data.get("error"),
    }
    gs = data.get("globals_schema") or {}
    if gs.get("artifact_outputs_path"):
        result["artifact_outputs_path"] = gs["artifact_outputs_path"]
    if data.get("hlig_graph") is not None:
        hlig = copy.deepcopy(data["hlig_graph"])
        result["hlig_graph"] = _enrich_hlig_graph(hlig)
        result["causal_paths"] = _compute_causal_paths(hlig)
    return result


@router.post("/runs")
async def create_run(req: RunRequest, background_tasks: BackgroundTasks):
    """Create a new run and start agent pipeline in background."""
    run_id = str(int(datetime.utcnow().timestamp() * 1000))[-10:]
    active_sessions[run_id] = {"status": "starting", "query": req.query}
    background_tasks.add_task(process_run, run_id, req.query, req.pipeline)
    return {
        "id": run_id,
        "status": "starting",
        "created_at": datetime.utcnow().isoformat(),
        "query": req.query,
    }


@router.get("/runs")
async def list_runs(limit: int = 50):
    """List recent runs (sessions)."""
    sessions = session_manager.list_sessions(limit=limit)
    # Merge with active runs
    result = []
    seen = set()
    for rid, info in active_sessions.items():
        if isinstance(info, dict) and rid not in seen:
            seen.add(rid)
            result.append({
                "id": rid,
                "status": info.get("status", "running"),
                "created_at": datetime.utcnow().isoformat(),
                "query": info.get("query", ""),
            })
    for s in sessions:
        sid = s.get("session_id", "").replace("session_", "")
        if sid not in seen:
            seen.add(sid)
            result.append({
                "id": sid,
                "status": s.get("status", "completed"),
                "created_at": s.get("created_at", ""),
                "query": s.get("query", ""),
            })
    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return result[:limit]


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    """Get run status and context."""
    if run_id in active_sessions:
        info = active_sessions[run_id]
        if isinstance(info, dict):
            # Try to load saved progress if any
            saved = session_manager.load_session(run_id)
            if saved:
                saved["session_id"] = run_id
                r = _run_to_response(saved)
                r["pending_clarification"] = info.get("pending_clarification") or saved.get("pending_clarification")
                return r
            return {
                "id": run_id,
                "status": info.get("status", "running"),
                "created_at": info.get("created_at", datetime.utcnow().isoformat()),
                "query": info.get("query", ""),
                "artifacts": info.get("artifacts", {}),
                "runs": info.get("runs", []),
                "pending_clarification": info.get("pending_clarification"),
                "messages": _build_messages({**info, "query": info.get("query"), "globals_schema": info.get("globals_schema", {})}),
                "error": info.get("error"),
            }
    data = session_manager.load_session(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Run not found")
    data["session_id"] = run_id
    return _run_to_response(data)


@router.post("/runs/{run_id}/input")
async def provide_input(run_id: str, req: InputRequest):
    """Provide user input (e.g. clarification response) to unblock a waiting run."""
    if run_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Run not found or not active")
    info = active_sessions[run_id]
    if not isinstance(info, dict):
        raise HTTPException(status_code=400, detail="Invalid run state")
    ev = info.get("input_event")
    val = info.get("input_value")
    if not ev or val is None:
        raise HTTPException(status_code=400, detail="Run is not waiting for input")
    val.append(req.response)
    log_pipeline_event(run_id, "api_input_received", {"response_length": len(req.response)})
    ev.set()
    return {"id": run_id, "status": "input_received"}
