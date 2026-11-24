"""Standalone customer agent server for the AI Call Agent (Katie) to call.

    python -m customer_agent.server              (port 8020)
    python -m customer_agent.server --port 8010  (to replace the old customer agent on its default port)

Then in C:\\AI_Call_Agent\\backend\\.env set  CUSTOMER_AGENT_URL=http://localhost:8020
"""

from __future__ import annotations

import argparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router


def create_app() -> FastAPI:
    app = FastAPI(title="Realistic Customer Agent")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8020)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    uvicorn.run(app, host=a.host, port=a.port)
