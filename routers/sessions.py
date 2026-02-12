"""Sessions router - list, get, create sessions."""

from fastapi import APIRouter, HTTPException

from session.manager import SessionManager

router = APIRouter()
session_manager = SessionManager()


@router.get("/sessions")
async def list_sessions(limit: int = 50):
    """List recent sessions."""
    return session_manager.list_sessions(limit=limit)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Load a session by ID."""
    data = session_manager.load_session(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return data
