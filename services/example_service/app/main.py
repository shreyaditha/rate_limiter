from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

app = FastAPI(
    title="Example Upstream Service",
    description="A lightweight upstream microservice demonstrating reverse proxy routing, RBAC, and rate limiting.",
    version="1.0.0",
)

ITEMS = [
    {
        "id": "item_101",
        "name": "Cloud Server Pro",
        "category": "compute",
        "price": 49.99,
        "status": "active",
    },
    {
        "id": "item_102",
        "name": "Managed Redis Cluster",
        "category": "database",
        "price": 89.99,
        "status": "active",
    },
    {
        "id": "item_103",
        "name": "Global Edge CDN",
        "category": "networking",
        "price": 29.99,
        "status": "pending",
    },
]


class CreateItemRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    category: str = Field(..., min_length=1, max_length=50)
    price: float = Field(..., gt=0)
    status: Optional[str] = "active"


@app.get("/health")
def health() -> dict:
    """Service health check endpoint."""
    return {"service": "example_service", "status": "ok"}


@app.get("/items")
def list_items() -> dict:
    """List all available items."""
    return {"items": ITEMS, "total": len(ITEMS)}


@app.get("/items/{item_id}")
def get_item(item_id: str) -> dict:
    """Get item by its ID."""
    for item in ITEMS:
        if item["id"] == item_id:
            return item
    raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found")


@app.post("/items", status_code=201)
def create_item(body: CreateItemRequest, request: Request) -> dict:
    """Create a new item. Demonstrates role-based write access."""
    forwarded_user = request.headers.get("x-forwarded-user", "anonymous")
    new_item = {
        "id": f"item_{len(ITEMS) + 101}",
        "name": body.name,
        "category": body.category,
        "price": body.price,
        "status": body.status or "active",
        "created_by": forwarded_user,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    ITEMS.append(new_item)
    return new_item


@app.get("/admin/metrics")
def admin_metrics(request: Request) -> dict:
    """Admin-only telemetry endpoint to demonstrate route-level RBAC."""
    return {
        "metrics": {
            "uptime_seconds": 3600,
            "total_items": len(ITEMS),
            "memory_usage_mb": 42.5,
            "forwarded_role": request.headers.get("x-forwarded-role", "unknown"),
        }
    }
