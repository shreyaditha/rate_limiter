from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Users Service", version="1.0.0")

USERS = [
    {"id": "usr_alice", "username": "alice", "role": "admin"},
    {"id": "usr_bob", "username": "bob", "role": "user"},
]


class CreateUser(BaseModel):
    username: str
    role: str = "user"


@app.get("/health")
def health() -> dict:
    return {"service": "users", "status": "ok"}


@app.get("/")
def list_users() -> dict:
    return {"users": USERS}


@app.get("/{user_id}")
def get_user(user_id: str) -> dict:
    for user in USERS:
        if user["id"] == user_id:
            return user
    return {"id": user_id, "status": "not_found"}


@app.post("/")
def create_user(body: CreateUser) -> dict:
    return {"id": "usr_new", "username": body.username, "role": body.role, "status": "created"}
