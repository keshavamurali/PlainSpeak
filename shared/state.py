# Shared State Module
# Holds global state shared across routers and components

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Active sessions/runs - session_id -> session or loop reference
active_sessions: dict[str, object] = {}

# MCP instance - initialized in api.py lifespan
_multi_mcp = None


def get_multi_mcp():
    """Get the MultiMCP instance, creating it if needed."""
    global _multi_mcp
    if _multi_mcp is None:
        from mcp_servers.multi_mcp import MultiMCP
        _multi_mcp = MultiMCP()
    return _multi_mcp
