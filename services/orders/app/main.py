from datetime import datetime, timezone

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Orders Service", version="1.0.0")

ORDERS = [
    {"id": "ord_1001", "sku": "SKU-WIDGET", "qty": 2, "status": "shipped"},
    {"id": "ord_1002", "sku": "SKU-GADGET", "qty": 1, "status": "pending"},
]


class CreateOrder(BaseModel):
    sku: str
    qty: int = 1


@app.get("/health")
def health() -> dict:
    return {"service": "orders", "status": "ok"}


@app.get("/")
def list_orders() -> dict:
    return {"orders": ORDERS}


@app.get("/{order_id}")
def get_order(order_id: str) -> dict:
    for order in ORDERS:
        if order["id"] == order_id:
            return order
    return {"id": order_id, "status": "not_found"}


@app.post("/")
def create_order(body: CreateOrder) -> dict:
    return {
        "id": "ord_new",
        "sku": body.sku,
        "qty": body.qty,
        "status": "created",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
