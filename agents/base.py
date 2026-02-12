"""Base agent class for the multi-agent framework."""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from context.execution_context import ExecutionContext

if TYPE_CHECKING:
    from mcp_servers.multi_mcp import MultiMCP


class BaseAgent:
    """
    Single agent class used for all agent types.
    Agent behavior is defined by the prompt; use different prompts for planner, coder, reviewer, etc.
    """

    def __init__(
        self,
        name: str,
        prompt: str | None = None,
        prompt_file: str | Path | None = None,
        config: dict[str, Any] | None = None,
        multi_mcp: "MultiMCP | None" = None,
    ):
        self.name = name
        self.prompt = prompt
        self.prompt_file = Path(prompt_file) if prompt_file else None
        self.config = config or {}
        self.multi_mcp = multi_mcp  # Optional: for tool calls

    def _load_prompt(self) -> str:
        """Load prompt from file or return the one set directly."""
        if self.prompt:
            return self.prompt
        if self.prompt_file and self.prompt_file.exists():
            return self.prompt_file.read_text()
        return ""

    def run(self, ctx: ExecutionContext) -> ExecutionContext:
        """
        Execute the agent with the current prompt.

        Args:
            ctx: Current execution context (input state, history, artifacts)

        Returns:
            Updated execution context after agent execution
        """
        prompt_text = self._load_prompt()
        # Placeholder: integrate with LLM here. Pass prompt_text + ctx.state/artifacts
        ctx.add_artifact(self.name, {"prompt": prompt_text, "output": "TODO: LLM output"})
        return ctx

    def __repr__(self) -> str:
        return f"BaseAgent(name={self.name!r})"
