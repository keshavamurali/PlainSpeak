"""MultiMCP - manages MCP server connections and tool routing."""

import asyncio
import json
import os
import shutil
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool


class MultiMCP:
    """Manages multiple MCP servers and routes tool calls."""

    def __init__(self):
        self.exit_stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}
        self.tools: dict[str, list[Tool]] = {}
        self.base_dir = Path(__file__).parent
        self.config_path = self.base_dir / "mcp_config.json"
        self.server_configs = self._load_config()

    def _load_config(self) -> dict:
        """Load server configuration from JSON."""
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text())
            except Exception as e:
                print(f"⚠️ Failed to load MCP config: {e}")
        return {}

    def _save_config(self) -> None:
        """Save current server configuration."""
        try:
            self.config_path.write_text(json.dumps(self.server_configs, indent=2))
        except Exception as e:
            print(f"⚠️ Failed to save MCP config: {e}")

    async def _start_server(self, name: str, config: dict) -> None:
        """Start a single MCP server."""
        if config.get("enabled", True) is False:
            print(f"  ⏭️ Server '{name}' disabled. Skipping.")
            return

        try:
            cmd = config.get("command", "uv")
            args = config.get("args", [])
            server_type = config.get("type", "local-script")
            env = config.get("env")

            if server_type == "local-script":
                script_name = args[-1] if args else ""
                if script_name and not Path(script_name).is_absolute():
                    script_path = self.base_dir / script_name
                    if script_path.exists():
                        args = args[:-1] + [str(script_path)]

            final_env = os.environ.copy()
            if env:
                final_env.update(env)

            if cmd == "uv" and not shutil.which("uv"):
                cmd = sys.executable
                print(f"  ⚠️ 'uv' not found. Falling back to python for {name}.")

            server_params = StdioServerParameters(
                command=cmd,
                args=args,
                env=final_env or None,
            )

            async with asyncio.timeout(20):
                read, write = await self.exit_stack.enter_async_context(
                    stdio_client(server_params)
                )
                session = await self.exit_stack.enter_async_context(
                    ClientSession(read, write)
                )
                await session.initialize()
                result = await session.list_tools()
                self.tools[name] = result.tools
                self.sessions[name] = session
                print(f"  ✅ [cyan]{name}[/cyan] connected. Tools: {len(result.tools)}")

        except TimeoutError:
            print(f"  ⏳ [yellow]{name}[/yellow] timed out during startup.")
        except Exception as e:
            print(f"  ❌ [red]{name}[/red] failed: {e}")

    async def start(self) -> None:
        """Start all configured servers."""
        print("🚀 Starting MCP Servers...")
        for name, config in self.server_configs.items():
            if config.get("enabled", True):
                await self._start_server(name, config)
            else:
                print(f"  ⏭️ Skipping disabled: {name}")

    async def stop(self) -> None:
        """Stop all servers."""
        print("🛑 Stopping MCP Servers...")
        await self.exit_stack.aclose()

    def get_all_tools(self) -> list[Tool]:
        """Get all tools from connected servers."""
        all_tools = []
        for tools in self.tools.values():
            all_tools.extend(tools)
        return all_tools

    def get_connected_servers(self) -> list[str]:
        """Return list of connected server names."""
        return list(self.sessions.keys())

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict
    ):
        """Call a tool on a specific server."""
        if server_name not in self.sessions:
            raise ValueError(f"Server '{server_name}' not connected")
        return await self.sessions[server_name].call_tool(tool_name, arguments)

    async def route_tool_call(self, tool_name: str, arguments: dict):
        """Route a tool call to the server that has the tool."""
        for name, tools in self.tools.items():
            for tool in tools:
                if tool.name == tool_name:
                    return await self.call_tool(name, tool_name, arguments)
        raise ValueError(f"Tool '{tool_name}' not found in any server")
