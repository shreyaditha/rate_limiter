"""
Route-prefix RBAC.

Roles
-----
admin : all methods on /orders, /inventory, /users
user  : read-only GET/HEAD/OPTIONS on /orders and /inventory
        writes and the entire /users tree are forbidden
"""

from app.schemas import UserClaims

# Prefixes the gateway will proxy. /auth and /health are handled locally.
PROTECTED_PREFIXES = ("/orders", "/inventory", "/users")

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def is_allowed(claims: UserClaims, method: str, path: str) -> bool:
    if claims.role == "admin":
        return True

    if claims.role != "user":
        return False

    if path == "/auth/me" or path.startswith("/auth/me?"):
        return True

    if path.startswith("/users"):
        return False

    if path.startswith("/orders") or path.startswith("/inventory"):
        return method.upper() not in _WRITE_METHODS

    return False
