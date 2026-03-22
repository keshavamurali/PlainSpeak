"""Base agent class for the multi-agent framework."""

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context.execution_context import ExecutionContext

try:
    from core.debug_logger import log_llm_call, log_llm_input, log_step_end, log_step_start, check_cost_limit_before_llm
except ImportError:
    log_step_start = log_step_end = log_llm_call = lambda *a, **kw: None
    log_llm_input = lambda *a, **kw: None
    def check_cost_limit_before_llm(_session_id: str) -> None: ...

if TYPE_CHECKING:
    from mcp_servers.multi_mcp import MultiMCP


class BaseAgent:
    """
    Single agent class used for all agent types.
    Agent behavior is defined by the prompt; uses LLM (Gemini/Ollama) when configured.
    """

    def __init__(
        self,
        name: str,
        prompt: str | None = None,
        prompt_file: str | Path | None = None,
        config: dict[str, Any] | None = None,
        multi_mcp: "MultiMCP | None" = None,
        reads: list[str] | None = None,
        writes: list[str] | None = None,
    ):
        self.name = name
        self.prompt = prompt
        self.prompt_file = Path(prompt_file) if prompt_file else None
        self.config = config or {}
        self.multi_mcp = multi_mcp
        self.reads = reads or []
        self.writes = writes or []

    def _load_prompt(self) -> str:
        """Load prompt from file or return the one set directly."""
        if self.prompt:
            return self.prompt
        if self.prompt_file and self.prompt_file.exists():
            return self.prompt_file.read_text()
        return ""

    def _build_input_data(self, ctx: ExecutionContext) -> dict:
        """Build input payload from globals_schema using reads."""
        input_data = {}
        for key in self.reads:
            if key in ctx.globals_schema:
                input_data[key] = ctx.globals_schema[key]
        # Always include original_query for context
        if "original_query" not in input_data and "original_query" in ctx.globals_schema:
            input_data["original_query"] = ctx.globals_schema["original_query"]
        if "original_query" not in input_data:
            input_data["original_query"] = ctx.get_state("query", "")
        return input_data

    def _call_llm(self, prompt_text: str, input_data: dict, session_id: str = "") -> dict | str:
        """Call LLM and parse response. Returns dict or raw string on parse failure."""
        check_cost_limit_before_llm(session_id)
        log_llm_input(session_id, self.name, input_data)
        try:
            from core.model_manager import ModelManager
            from core.json_parser import parse_llm_json, JsonParsingError
        except ImportError as e:
            return {"_error": f"LLM not available: {e}"}

        try:
            model_manager = ModelManager()
        except Exception as e:
            return {"_error": str(e)}

        # Planner: inject USER_PROJECT_REQUEST block (prompts/planner.md format)
        if self.name == "planner":
            query = str(input_data.get("original_query", ""))
            clarification = input_data.get("user_clarification", "")
            block_content = query
            if clarification:
                block_content = f"{query}\n\n[User's clarification responses:]\n{clarification}"
            full_prompt = re.sub(
                r"<USER_PROJECT_REQUEST>.*?</USER_PROJECT_REQUEST>",
                f"<USER_PROJECT_REQUEST>\n{block_content}\n</USER_PROJECT_REQUEST>",
                prompt_text,
                count=1,
                flags=re.DOTALL,
            )
        elif self.name == "designer":
            writes_hint = "\n\nYour output must be valid JSON with keys: hlig_node_id, nodes, edges."
            full_prompt = f"{prompt_text.strip()}{writes_hint}\n\n## HLIG Node (input)\n\n```json\n{json.dumps(input_data, indent=2)}\n```"
        else:
            writes_hint = ""
            if self.writes:
                writes_hint = "\n\nYour output should use the following variable name(s): " + ", ".join(self.writes)
            full_prompt = f"{prompt_text.strip()}{writes_hint}\n\n```json\n{json.dumps(input_data, indent=2)}\n```"

        try:
            response_text, usage = model_manager.generate_text(full_prompt)
            usage_dict = usage._asdict() if usage else None
            # Log only variable input (not prompt template from .md) to keep debug logs smaller
            if self.name == "planner":
                variable_input = block_content
            else:
                variable_input = json.dumps(input_data, indent=2)
            log_llm_call(
                session_id, self.name, full_prompt, response_text,
                usage=usage_dict, variable_input=variable_input,
            )
            response = response_text
        except Exception as e:
            return {"_error": str(e)}

        try:
            output = parse_llm_json(response)
            if isinstance(output, list) and len(output) > 0 and isinstance(output[0], dict):
                output = output[0]
            return output
        except JsonParsingError:
            return {"output": response, "_raw": True}

    def run(self, ctx: ExecutionContext, input_override: dict | None = None) -> ExecutionContext:
        """
        Execute the agent with the current prompt.
        Uses LLM when configured; falls back to placeholder if not.
        If input_override is provided, use it instead of building from ctx (for designer, etc.).
        """
        prompt_text = self._load_prompt()
        input_data = input_override if input_override is not None else self._build_input_data(ctx)
        session_id = getattr(ctx, "session_id", "") or ""

        log_step_start(session_id, self.name, input_data)

        output = self._call_llm(prompt_text, input_data, session_id)

        if isinstance(output, dict) and "_error" in output:
            output = "TODO: LLM output"  # Fallback when LLM unavailable
        elif isinstance(output, dict) and "_raw" in output:
            output = output.get("output", "TODO: LLM output")

        log_step_end(session_id, self.name, output)
        ctx.add_artifact(self.name, {"prompt": prompt_text, "output": output})
        return ctx

    def __repr__(self) -> str:
        return f"BaseAgent(name={self.name!r})"
