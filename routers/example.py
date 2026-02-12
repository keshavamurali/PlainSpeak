from fastapi import APIRouter

router = APIRouter()


@router.get("/ping")
async def ping():
    """Skeletal endpoint - replace or add routes as needed."""
    return {"message": "pong"}
