"""Config-driven agent runner. Runs the base agent with any prompt."""

from pathlib import Path
from typing import Any

from context.execution_context import ExecutionContext
from agents.base import BaseAgent

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_event_bus():
    try:
        from core.event_bus import event_bus
        return event_bus
    except ImportError:
        return None


class AgentRunner:
    """Runs the agent with prompts from config. Agent types are defined by prompts."""

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
        """Load agent configuration from YAML file."""
        import yaml

        if not self.config_path.exists():
            raise FileNotFoundError(f"Config not found: {self.config_path}")

        with open(self.config_path) as f:
            return yaml.safe_load(f) or {}

    def run(
        self,
        ctx: ExecutionContext | None = None,
        pipeline: str | list[str] | None = None,
        event_loop=None,
        on_step_complete=None,
    ) -> ExecutionContext:
        """
        Run the agent pipeline. Each step uses the base agent with a different prompt.

        Args:
            ctx: Initial execution context. Created with default if None.
            pipeline: Pipeline name from config, or list of step names to run.
                      If None, runs the default pipeline.

        Returns:
            Final execution context after all steps have run.
        """
        self._loop = event_loop
        if ctx is None:
            ctx = ExecutionContext.create()

        pipelines = self.config.get("pipelines", {})
        if pipeline is None:
            pipeline = self.config.get("default_pipeline", "default")

        if isinstance(pipeline, str):
            step_names = pipelines.get(pipeline, [pipeline])
        else:
            step_names = pipeline

        steps = {s["name"]: s for s in self.config.get("steps", [])}

        bus = _get_event_bus()
        for step_name in step_names:
            step_spec = steps.get(step_name)
            if not step_spec:
                raise ValueError(f"Unknown step: {step_name}")
            prompt_file = step_spec.get("prompt_file")
            if prompt_file:
                prompt_file = PROJECT_ROOT / prompt_file
            agent = BaseAgent(
                name=step_spec["name"],
                prompt_file=prompt_file,
                prompt=step_spec.get("prompt"),
                config=step_spec.get("config", {}),
                multi_mcp=getattr(self, "_multi_mcp", None),
            )
            ctx = agent.run(ctx)
            ctx.record_agent_run(step_name, agent)
            if on_step_complete:
                try:
                    on_step_complete(ctx)
                except Exception:
                    pass
            if bus and self._loop:
                import asyncio
                fut = asyncio.run_coroutine_threadsafe(
                    bus.publish("step_completed", step_name, {
                        "step": step_name,
                        "session_id": ctx.session_id,
                        "artifacts": dict(ctx.artifacts),
                    }),
                    self._loop,
                )
                try:
                    fut.result(timeout=2)
                except Exception:
                    pass

        return ctx
