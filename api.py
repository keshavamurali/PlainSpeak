import sys
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 API Starting up...")
    # Start MCP servers
    from shared.state import get_multi_mcp
    multi_mcp = get_multi_mcp()
    try:
        await multi_mcp.start()
    except Exception as e:
        print(f"⚠️ MCP startup warning: {e}")
    yield
    print("🛑 API Shutting down...")
    await multi_mcp.stop()


app = FastAPI(lifespan=lifespan)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "app://."],
    allow_origin_regex=r"http://localhost:(517\d|5555)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Include Routers (add more as needed) ===
from routers import example as example_router
from routers import sessions as sessions_router
from routers import mcp as mcp_router
from routers import runs as runs_router
from routers import stream as stream_router

app.include_router(example_router.router, prefix="/api", tags=["example"])
app.include_router(sessions_router.router, prefix="/api", tags=["sessions"])
app.include_router(mcp_router.router, prefix="/api", tags=["mcp"])
app.include_router(runs_router.router, prefix="/api", tags=["runs"])
app.include_router(stream_router.router, prefix="/api", tags=["stream"])


@app.get("/health")
async def health_check():
    from shared.state import get_multi_mcp
    mcp = get_multi_mcp()
    return {
        "status": "ok",
        "version": "1.0.0",
        "mcp_servers": mcp.get_connected_servers(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
