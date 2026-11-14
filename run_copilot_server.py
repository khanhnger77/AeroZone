"""Standalone FastAPI runner for Odyssey Copilot."""
import os
import sys

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from odyssey_copilot.backend.router import router as copilot_router

app = FastAPI(title="Odyssey Copilot API", version="1.0.0", description="NASA EVA Assistant API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(copilot_router, prefix="/api/copilot")


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Odyssey Copilot",
        "docs_url": "http://127.0.0.1:8001/docs",
        "chat_endpoint": "http://127.0.0.1:8001/api/copilot/chat"
    }


if __name__ == "__main__":
    print("🚀 Starting Odyssey Copilot API at http://127.0.0.1:8001")
    print("📖 Interactive API Docs (Swagger): http://127.0.0.1:8001/docs")
    uvicorn.run("run_copilot_server:app", host="127.0.0.1", port=8001, reload=True)
