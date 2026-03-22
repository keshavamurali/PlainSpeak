"""Session logger - writes run logs to session_log directory."""

import json
from datetime import datetime
from pathlib import Path

from context.execution_context import ExecutionContext

# Logs stored under project root / session_log
LOGS_DIR = Path(__file__).parent


class SessionLogger:
    """Logs execution context and sessions to disk."""

    def __init__(self, base_dir: Path | str | None = None):
        self.base_dir = Path(base_dir) if base_dir else LOGS_DIR

    def _session_dir(self, session_id: str) -> Path:
        """Get or create session directory."""
        session_dir = self.base_dir / f"session_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def log_context(self, ctx: ExecutionContext, suffix: str = "") -> Path:
        """
        Log execution context to a JSON file.

        Args:
            ctx: Execution context to log
            suffix: Optional suffix for filename (e.g. '_final', '_step_1')

        Returns:
            Path to the written log file
        """
        session_dir = self._session_dir(ctx.session_id)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"run_{timestamp}{suffix}.json"
        filepath = session_dir / filename

        with open(filepath, "w") as f:
            json.dump(ctx.to_dict(), f, indent=2)

        return filepath

    def log_message(self, session_id: str, message: str, level: str = "info") -> None:
        """Append a line to the session's message log."""
        session_dir = self._session_dir(session_id)
        log_path = session_dir / "messages.log"
        timestamp = datetime.utcnow().isoformat()
        line = f"[{timestamp}] [{level.upper()}] {message}\n"
        with open(log_path, "a") as f:
            f.write(line)
