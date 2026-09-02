"""
Route-prefix Role-Based Access Control (RBAC).

Roles
-----
admin : all methods on /items, /admin, and all upstream routes.
user  : read-only (GET/HEAD/OPTIONS) on standard resources like /items.
        writes (POST/PUT/PATCH/DELETE) and administrative paths like /admin are forbidden (403).
"""

from app.schemas import UserClaims

# Prefixes the gateway proxies that require authentication and RBAC enforcement.
# Local endpoints like /auth and /health are handled separately.
PROTECTED_PREFIXES = ("/items", "/admin")

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def is_allowed(claims: UserClaims, method: str, path: str) -> bool:
    """
    Evaluates whether the caller's role is authorized to perform the HTTP method on the path.
    """
    if claims.role == "admin":
        return True

    if claims.role != "user":
        return False

    if path == "/auth/me" or path.startswith("/auth/me?"):
        return True

    # Regular users cannot access admin routes
    if path.startswith("/admin"):
        return False

    # Regular users have read-only access to standard resources (/items)
    if path.startswith("/items"):
        return method.upper() not in _WRITE_METHODS

    return False
