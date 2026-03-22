"""MCP router - list servers and tools."""

from fastapi import APIRouter, HTTPException, Body

from shared.state import get_multi_mcp

router = APIRouter()


@router.get("/mcp/servers")
async def list_servers():
    """List connected MCP servers."""
    mcp = get_multi_mcp()
    return {"servers": mcp.get_connected_servers()}


@router.get("/mcp/tools")
async def list_tools():
    """List all tools from connected MCP servers."""
    mcp = get_multi_mcp()
    tools = mcp.get_all_tools()
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema,
            }
            for t in tools
        ]
    }


@router.post("/mcp/tools/{tool_name}/call")
async def call_tool(tool_name: str, arguments: dict = Body(default_factory=dict)):
    """Call an MCP tool by name (routes to correct server)."""
    mcp = get_multi_mcp()
    try:
        result = await mcp.route_tool_call(tool_name, arguments or {})
        if hasattr(result, "content") and result.content:
            return {"content": [{"text": c.text} for c in result.content]}
        return {"result": str(result)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
