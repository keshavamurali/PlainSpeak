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
    # Add startup logic here (DB, caches, etc.)
    yield
    print("🛑 API Shutting down...")
    # Add shutdown logic here


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

app.include_router(example_router.router, prefix="/api", tags=["example"])


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "version": "1.0.0",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
