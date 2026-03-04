"""Debug logging - writes step I/O and LLM I/O to debug_logs/, session-based like session_log."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEBUG_LOGS_BASE = PROJECT_ROOT / "debug_logs" / "sessions"
MAX_LOG_BODY_CHARS = 8000  # Truncate very long content for readability


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


def _truncate(s: str, max_len: int = MAX_LOG_BODY_CHARS) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"\n... [truncated, total {len(s)} chars]"


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


def log_llm_call(session_id: str, agent_name: str, prompt: str, response: str) -> None:
    """Log LLM input (prompt) and output (response)."""
    ts = _timestamp()
    prompt_str = _truncate(prompt)
    response_str = _truncate(response)
    entry = f"\n{'='*80}\n[{ts}] LLM_CALL   | session={session_id} | agent={agent_name}\n"
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


def log_user_input(session_id: str, node_id: str, message: str, user_response: str) -> None:
    """Log user clarification input and response."""
    ts = _timestamp()
    entry = f"[{ts}] USER_INPUT | session={session_id} | node={node_id}\n"
    entry += f"  MESSAGE: {_truncate(message, 500)}\n"
    entry += f"  USER_RESPONSE: {_truncate(user_response, 2000)}\n"
    _write(session_id, entry)
