"""Debug logging - writes step I/O and LLM I/O to debug_logs/, session-based like session_log."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEBUG_LOGS_BASE = PROJECT_ROOT / "debug_logs" / "sessions"
# Truncation: DEBUG_LOG_MAX_CHARS env (default 8000). Use 0 for no truncation.
_DEFAULT_MAX = 8000
# Cost limit: COST_LIMIT_USD env (default 0.25). Run stops when exceeded. Use 0 to disable.
_COST_LIMIT = 0.25
try:
    _cl = os.environ.get("COST_LIMIT_USD", "")
    _COST_LIMIT = float(_cl) if _cl else 0.25
except (ValueError, TypeError):
    _COST_LIMIT = 0.25


class CostLimitExceeded(Exception):
    """Raised when session LLM cost exceeds COST_LIMIT_USD. Stops the run."""

    def __init__(self, session_id: str, cost_usd: float, limit_usd: float):
        self.session_id = session_id
        self.cost_usd = cost_usd
        self.limit_usd = limit_usd
        super().__init__(
            f"Cost limit exceeded: run {session_id} cost ${cost_usd:.4f} exceeds limit ${limit_usd:.2f}. Run stopped."
        )


_env_val = os.environ.get("DEBUG_LOG_MAX_CHARS", "")
try:
    _n = int(_env_val) if _env_val else _DEFAULT_MAX
    MAX_LOG_BODY_CHARS = _n if _n > 0 else 0
except ValueError:
    MAX_LOG_BODY_CHARS = _DEFAULT_MAX


def _date_dir(dt: datetime | None = None) -> Path:
    """Get date directory: debug_logs/sessions/YYYY/MM/DD/."""
    dt = dt or datetime.utcnow()
    d = DEBUG_LOGS_BASE / str(dt.year) / f"{dt.month:02d}" / f"{dt.day:02d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_path(session_id: str) -> Path:
    """Path to debug log file for a session: debug_session_<id>.log"""
    sid = session_id.strip() or "no_session"
    return _date_dir() / f"debug_session_{sid}.log"


def _graph_execution_trace_path(session_id: str) -> Path:
    """Path for plan-DAG execution trace: graph_execution_trace_<id>.log (under debug_logs/sessions/YYYY/MM/DD/)."""
    sid = session_id.strip() or "no_session"
    return _date_dir() / f"graph_execution_trace_{sid}.log"


def _graph_execution_trace_enabled() -> bool:
    """GRAPH_EXECUTION_TRACE defaults to on; set to 0/false/no to disable."""
    v = os.environ.get("GRAPH_EXECUTION_TRACE", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _truncate(s: str, max_len: int | None = None) -> str:
    limit = max_len if max_len is not None else MAX_LOG_BODY_CHARS
    if limit <= 0 or len(s) <= limit:
        return s
    return s[:limit] + f"\n... [truncated, total {len(s)} chars]"


def _format_value(val: Any, max_len: int = MAX_LOG_BODY_CHARS) -> str:
    if isinstance(val, (dict, list)):
        s = json.dumps(val, indent=2, default=str)
    else:
        s = str(val)
    return _truncate(s, max_len)


def _timestamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _write(session_id: str, entry: str) -> None:
    try:
        log_path = _log_path(session_id)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except OSError:
        pass


def log_step_start(session_id: str, step_name: str, input_data: dict | Any) -> None:
    """Log the start of an agent step with its input."""
    ts = _timestamp()
    inp = _format_value(input_data) if input_data is not None else "(none)"
    entry = f"\n{'='*80}\n[{ts}] STEP_START | session={session_id} | agent={step_name}\n"
    entry += f"INPUT:\n{inp}\n"
    _write(session_id, entry)


def log_step_end(session_id: str, step_name: str, output: dict | Any) -> None:
    """Log the end of an agent step with its output."""
    ts = _timestamp()
    out = _format_value(output) if output is not None else "(none)"
    entry = f"[{ts}] STEP_END   | session={session_id} | agent={step_name}\n"
    entry += f"OUTPUT:\n{out}\n"
    _write(session_id, entry)


# Per-session token/cost accumulator for run summaries
_session_usage: dict[str, dict] = {}


def accumulate_usage(session_id: str, usage: dict) -> None:
    """Accumulate token usage for session summary. Raises CostLimitExceeded if limit exceeded."""
    if not session_id or not usage:
        return
    key = session_id.strip() or "no_session"
    if key not in _session_usage:
        _session_usage[key] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
    acc = _session_usage[key]
    acc["input_tokens"] += usage.get("input_tokens", 0)
    acc["output_tokens"] += usage.get("output_tokens", 0)
    acc["total_tokens"] += usage.get("total_tokens", 0)
    acc["cost_usd"] += usage.get("cost_usd", 0.0)
    if _COST_LIMIT > 0 and acc["cost_usd"] > _COST_LIMIT:
        raise CostLimitExceeded(session_id, acc["cost_usd"], _COST_LIMIT)


def get_session_usage(session_id: str) -> dict:
    """Get accumulated usage for a session. Returns empty dict if none."""
    key = (session_id or "").strip() or "no_session"
    return dict(_session_usage.get(key, {}))


def get_cost_limit_usd() -> float:
    """Return configured cost limit in USD (0 disables limit)."""
    return float(_COST_LIMIT)


def check_cost_limit_before_llm(session_id: str) -> None:
    """
    If session cost already >= limit, raise CostLimitExceeded so caller skips the LLM call.
    Call this at the start of _call_llm to avoid making a request when limit is already exceeded.
    """
    if not session_id or _COST_LIMIT <= 0:
        return
    totals = get_session_usage(session_id)
    if totals.get("cost_usd", 0) >= _COST_LIMIT:
        raise CostLimitExceeded(session_id, totals["cost_usd"], _COST_LIMIT)


def log_llm_input(session_id: str, agent_name: str, input_content: dict | str) -> None:
    """Log the input sent to the LLM (before the call). Use a clear header so logs show it is input to LLM."""
    ts = _timestamp()
    entry = f"\n{'='*80}\n[{ts}] INPUT TO LLM | session={session_id} | agent={agent_name}\n"
    entry += "The following is the input payload or prompt content sent to the LLM:\n"
    entry += "-" * 40 + "\n"
    if isinstance(input_content, dict):
        entry += _format_value(input_content) + "\n"
    else:
        entry += _truncate(str(input_content)) + "\n"
    entry += "-" * 40 + "\n"
    _write(session_id, entry)


def log_llm_call(
    session_id: str,
    agent_name: str,
    prompt: str,
    response: str,
    usage: dict | None = None,
    variable_input: str | None = None,
) -> None:
    """Log LLM output (response) and token usage. Logs only variable_input (not prompt template) when provided."""
    if usage:
        accumulate_usage(session_id, usage)
    ts = _timestamp()
    response_str = _truncate(response)
    entry = f"\n{'='*80}\n[{ts}] LLM_CALL   | session={session_id} | agent={agent_name}\n"
    if usage:
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        total = usage.get("total_tokens", inp + out)
        cost = usage.get("cost_usd", 0)
        model = usage.get("model", "")
        entry += f"USAGE: input={inp} output={out} total={total} cost=${cost:.6f} model={model}\n"
        if session_id:
            totals = get_session_usage(session_id)
            cum_cost = totals.get("cost_usd", 0)
            cum_in = totals.get("input_tokens", 0)
            cum_out = totals.get("output_tokens", 0)
            cum_total = totals.get("total_tokens", cum_in + cum_out)
            entry += (
                "CUMULATIVE: "
                f"input={cum_in} output={cum_out} total={cum_total} "
                f"cost=${cum_cost:.6f} (this run so far)\n"
            )
    if variable_input is not None:
        inp_str = _truncate(variable_input)
        entry += f"INPUT (variable part only, prompt template omitted, {len(variable_input)} chars):\n{inp_str}\n"
    else:
        prompt_str = _truncate(prompt)
        entry += f"PROMPT ({len(prompt)} chars):\n{prompt_str}\n"
    entry += f"RESPONSE ({len(response)} chars):\n{response_str}\n"
    _write(session_id, entry)


def log_pipeline_event(session_id: str, event: str, details: str | dict | None = None) -> None:
    """Log pipeline/runner events (phase change, node start, user input, etc.)."""
    ts = _timestamp()
    entry = f"[{ts}] PIPELINE   | session={session_id} | {event}\n"
    if details:
        entry += f"  {_format_value(details, max_len=2000)}\n"
    _write(session_id, entry)


def log_graph_execution_trace(session_id: str, event: str, details: dict | None = None) -> None:
    """
    Append a graph / plan-DAG execution trace line to a dedicated file (not mixed with LLM debug spam).

    File: debug_logs/sessions/YYYY/MM/DD/graph_execution_trace_<session_id>.log
    Disable: GRAPH_EXECUTION_TRACE=0

    Events (emitted by AgentRunner): run_started, phase1_planner_done, plan_initialized,
    node_started, node_skipped, node_waiting_input, node_completed, node_failed, run_stopped,
    run_finished.
    """
    if not _graph_execution_trace_enabled():
        return
    try:
        ts = _timestamp()
        entry = f"[{ts}] TRACE | session={session_id} | {event}\n"
        if details:
            entry += f"  {_format_value(details, max_len=6000)}\n"
        path = _graph_execution_trace_path(session_id)
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)
    except OSError:
        pass


def sanitize_plan_dict_for_trace(plan_dict: dict | None) -> dict:
    """Strip heavy fields from plan.to_dict() for trace logging."""
    if not plan_dict:
        return {"nodes": [], "edges": []}
    slim_nodes = []
    for n in plan_dict.get("nodes", []):
        nid = n.get("id")
        if nid == "ROOT":
            continue
        slim_nodes.append({
            "id": nid,
            "agent": n.get("agent"),
            "status": n.get("status"),
            "description": str(n.get("description", ""))[:160],
            "reads": n.get("reads"),
            "writes": n.get("writes"),
        })
    return {
        "nodes": slim_nodes,
        "edges": list(plan_dict.get("edges", [])),
    }


def log_token_summary(session_id: str, totals: dict) -> None:
    """Log session-level token and cost summary (call at run end)."""
    ts = _timestamp()
    cost_usd = totals.get("cost_usd", 0)
    entry = f"\n{'='*80}\n[{ts}] TOKEN_SUMMARY | session={session_id}\n"
    entry += f"  input_tokens={totals.get('input_tokens', 0)} output_tokens={totals.get('output_tokens', 0)} "
    entry += f"total={totals.get('total_tokens', 0)}\n"
    entry += f"  TOTAL RUN COST: ${cost_usd:.6f}\n"
    _write(session_id, entry)


def log_user_input(session_id: str, node_id: str, message: str, user_response: str) -> None:
    """Log user clarification input and response."""
    ts = _timestamp()
    entry = f"[{ts}] USER_INPUT | session={session_id} | node={node_id}\n"
    entry += f"  MESSAGE: {_truncate(message, 500)}\n"
    entry += f"  USER_RESPONSE: {_truncate(user_response, 2000)}\n"
    _write(session_id, entry)
