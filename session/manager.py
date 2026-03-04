"""Session manager - handles session create, load, save, list."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from shared.state import PROJECT_ROOT

SESSIONS_BASE = PROJECT_ROOT / "session_log" / "sessions"


class SessionManager:
    """Manages agent execution sessions - persistence and retrieval."""

    def __init__(self, base_dir: Path | str | None = None):
        self.base_dir = Path(base_dir) if base_dir else SESSIONS_BASE
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _date_dir(self, dt: datetime | None = None) -> Path:
        """Get directory for a date: year/month/day."""
        dt = dt or datetime.now()
        d = self.base_dir / str(dt.year) / f"{dt.month:02d}" / f"{dt.day:02d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def create_session(
        self,
        session_id: str | None = None,
        query: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Create a new session record.

        Returns:
            Session dict with session_id, created_at, query, metadata, status
        """
        import uuid
        sid = session_id or str(uuid.uuid4())[:8]
        session = {
            "session_id": sid,
            "created_at": datetime.utcnow().isoformat(),
            "query": query or "",
            "metadata": metadata or {},
            "status": "created",
        }
        return session

    def save_session(self, session_id: str, data: dict[str, Any]) -> Path:
        """
        Save session data to disk.

        Args:
            session_id: Session identifier
            data: Full session/context data to persist

        Returns:
            Path to saved file
        """
        date_dir = self._date_dir()
        filepath = date_dir / f"session_{session_id}.json"
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)
        if data.get("hlig_graph") is not None:
            self.save_graph(session_id, data["hlig_graph"], date_dir=date_dir)
        return filepath

    def save_graph(self, session_id: str, graph_data: dict[str, Any], date_dir: Path | None = None) -> Path:
        """
        Save HLIG and DTG graph data to a separate JSON file (same location as session logs).

        Args:
            session_id: Session identifier
            graph_data: HLIG graph dict (nodes with embedded DTGs, edges)
            date_dir: Optional date directory; uses current date if not provided

        Returns:
            Path to saved graph file
        """
        date_dir = date_dir or self._date_dir()
        filepath = date_dir / f"graph_{session_id}.json"
        with open(filepath, "w") as f:
            json.dump(graph_data, f, indent=2, default=str)
        return filepath

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        """
        Load session data by ID. Searches recent date dirs.

        Returns:
            Session data dict or None if not found
        """
        # Search in date-structured dirs (most recent first)
        for year_dir in sorted(self.base_dir.iterdir(), reverse=True):
            if not year_dir.is_dir():
                continue
            for month_dir in sorted(year_dir.iterdir(), reverse=True):
                if not month_dir.is_dir():
                    continue
                for day_dir in sorted(month_dir.iterdir(), reverse=True):
                    if not day_dir.is_dir():
                        continue
                    candidate = day_dir / f"session_{session_id}.json"
                    if candidate.exists():
                        with open(candidate) as f:
                            return json.load(f)
        return None

    def load_session_file(self, filepath: Path) -> dict[str, Any]:
        """Load session from explicit file path."""
        with open(filepath) as f:
            return json.load(f)

    def list_sessions(
        self,
        limit: int = 50,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """
        List recent sessions.

        Args:
            limit: Max number to return
            since: Only include sessions after this datetime

        Returns:
            List of session summaries {session_id, path, created_at, ...}
        """
        sessions = []
        if not self.base_dir.exists():
            return sessions
        for path in sorted(
            self.base_dir.rglob("session_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[: limit * 2]:  # fetch extra then trim
            try:
                with open(path) as f:
                    data = json.load(f)
                created = data.get("created_at") or data.get("session_id", "")
                if since and created:
                    try:
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        if dt.replace(tzinfo=None) < since.replace(tzinfo=None):
                            continue
                    except Exception:
                        pass
                file_id = path.stem.replace("session_", "")
                sessions.append({
                    "session_id": file_id,
                    "path": str(path),
                    "created_at": created,
                    "status": data.get("status", "unknown"),
                    "query": data.get("query", ""),
                })
                if len(sessions) >= limit:
                    break
            except Exception:
                continue
        return sessions
