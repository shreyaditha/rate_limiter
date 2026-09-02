from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    user_id: str
    username: str
    password: str
    role: str
    api_key: str


# In-memory demo store. Replace with a real identity provider in production.
USERS: dict[str, User] = {
    "alice": User(
        user_id="usr_alice",
        username="alice",
        password="alicepass",
        role="admin",
        api_key="alice-admin-key",
    ),
    "bob": User(
        user_id="usr_bob",
        username="bob",
        password="bobpass",
        role="user",
        api_key="bob-user-key",
    ),
}

API_KEYS: dict[str, User] = {u.api_key: u for u in USERS.values()}


def authenticate(username: str, password: str) -> User | None:
    user = USERS.get(username)
    if user is None or user.password != password:
        return None
    return user


def get_user_by_api_key(api_key: str) -> User | None:
    return API_KEYS.get(api_key)
