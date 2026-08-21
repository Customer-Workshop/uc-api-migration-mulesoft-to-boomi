"""Loopback HTTP wrapper for the offline process engine."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .engine import ProcessEngine


def create_app() -> FastAPI:
    app = FastAPI()
    repo_root = Path(__file__).resolve().parents[2]
    dsn = os.environ.get(
        "DATABASE_URL",
        "postgresql://employee_user:employee_pass@localhost:5432/employee_db",
    )
    conn = psycopg.connect(dsn)
    engine = ProcessEngine(repo_root / "boomi" / "components", conn)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def handle(request: Request, path: str):
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else None
        result = engine.handle(
            request.method,
            "/" + path,
            dict(request.headers),
            body,
        )
        return JSONResponse(content=result["body"], status_code=result["status"])

    return app


app = create_app


if __name__ == "__main__":
    uvicorn.run("runner.server:create_app", host="127.0.0.1", port=8090, factory=True)
