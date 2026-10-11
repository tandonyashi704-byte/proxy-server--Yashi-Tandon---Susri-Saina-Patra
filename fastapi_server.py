"""
FastAPI Upstream Test Server.
Demonstrates realistic web application behavior with caching headers,
POST payload validation, dynamic processing, and latency simulation.
"""

import time
import asyncio
from typing import List, Optional
import uvicorn
from fastapi import FastAPI, Response
from pydantic import BaseModel

app = FastAPI(
    title="FastAPI Origin Service",
    description="Upstream server for PyProxy testing and benchmarks",
    version="1.0.0",
)


class Item(BaseModel):
    name: str
    price: float
    is_offer: Optional[bool] = None
    tags: List[str] = []


@app.get("/")
def read_root():
    """Root health check endpoint."""
    return {
        "service": "FastAPI Origin Server",
        "status": "online",
        "timestamp": time.time(),
        "message": "Welcome to the upstream origin service!",
    }


@app.get("/cacheable")
def get_cacheable_data(response: Response):
    """
    Returns data with Cache-Control headers indicating it can be cached for 60 seconds.
    """
    response.headers["Cache-Control"] = "public, max-age=60"
    return {
        "type": "cacheable",
        "generated_at": time.time(),
        "content": "This response should be cached by PyProxy for subsequent requests.",
    }


@app.get("/dynamic")
def get_dynamic_data(response: Response):
    """
    Returns strictly non-cacheable dynamic data with no-store headers.
    """
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return {
        "type": "dynamic",
        "generated_at": time.time(),
        "content": "This response must NEVER be cached by PyProxy.",
    }


@app.post("/items")
def create_item(item: Item):
    """
    POST endpoint accepting and validating a JSON payload.
    """
    return {
        "status": "created",
        "received_item": item.model_dump() if hasattr(item, "model_dump") else item.dict(),
        "server_timestamp": time.time(),
    }


@app.get("/slow")
def get_slow_response(response: Response):
    """
    Simulates a heavy database query or processing task (150ms delay).
    Cacheable so that PyProxy cache hit speedup can be vividly measured.
    """
    time.sleep(0.15)
    response.headers["Cache-Control"] = "public, max-age=60"
    return {
        "status": "success",
        "computation_time_ms": 150,
        "message": "Heavy upstream computation complete.",
    }


@app.get("/restricted-admin")
def get_restricted_data():
    """Restricted endpoint for testing proxy path-blocking security filters."""
    return {"secret": "Top secret administrator credentials!"}


def run(host: str = "127.0.0.1", port: int = 8000):
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run FastAPI Upstream Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    args = parser.parse_args()
    run(args.host, args.port)
