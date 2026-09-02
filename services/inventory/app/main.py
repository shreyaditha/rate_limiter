from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Inventory Service", version="1.0.0")

ITEMS = [
    {"sku": "SKU-WIDGET", "name": "Widget", "on_hand": 42},
    {"sku": "SKU-GADGET", "name": "Gadget", "on_hand": 7},
]


class Restock(BaseModel):
    sku: str
    qty: int


@app.get("/health")
def health() -> dict:
    return {"service": "inventory", "status": "ok"}


@app.get("/")
def list_items() -> dict:
    return {"items": ITEMS}


@app.get("/{sku}")
def get_item(sku: str) -> dict:
    for item in ITEMS:
        if item["sku"] == sku:
            return item
    return {"sku": sku, "on_hand": 0, "status": "unknown_sku"}


@app.post("/restock")
def restock(body: Restock) -> dict:
    return {"sku": body.sku, "added": body.qty, "status": "accepted"}
