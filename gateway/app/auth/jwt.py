from datetime import datetime, timedelta, timezone

import jwt

from app.auth.users import User
from app.config import Settings
from app.schemas import UserClaims


def create_access_token(user: User, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.user_id,
        "username": user.username,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> UserClaims:
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )
    return UserClaims(
        user_id=str(payload["sub"]),
        username=str(payload["username"]),
        role=str(payload["role"]),
    )
