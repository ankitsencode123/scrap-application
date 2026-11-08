"""Section 6: security (passwords, JWT, auth dependencies)."""

import base64
import hashlib
import uuid
from datetime import timedelta
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.config import settings
from app.db.base import DB, utcnow
from app.db.enums import Role
from app.db.models import User
from app.schemas import TokenOut, UserOut

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX}/auth/token")


def _prehash(password: str) -> bytes:
    # sha256 + base64 sidesteps bcrypt's 72-byte limit safely
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_prehash(password), hashed.encode("utf-8"))
    except ValueError:
        return False


_DUMMY_HASH = hash_password("dummy-password-for-timing")  # equalises login timing for unknown emails


def _create_token(user_id: uuid.UUID, token_type: str, expires: timedelta) -> str:
    now = utcnow()
    payload = {"sub": str(user_id), "type": token_type, "iat": now, "exp": now + expires, "jti": uuid.uuid4().hex}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _decode_token(token: str, expected_type: str) -> uuid.UUID:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != expected_type:
            raise unauthorized
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise unauthorized


def decode_access_token(token: str) -> uuid.UUID:
    return _decode_token(token, "access")


def build_token_response(user: User, is_new_user: bool = False) -> TokenOut:
    return TokenOut(
        access_token=_create_token(user.id, "access", timedelta(minutes=settings.ACCESS_TOKEN_MINUTES)),
        refresh_token=_create_token(user.id, "refresh", timedelta(days=settings.REFRESH_TOKEN_DAYS)),
        expires_in=settings.ACCESS_TOKEN_MINUTES * 60,
        is_new_user=is_new_user,
        user=UserOut.model_validate(user),
    )


def get_current_user(db: DB, token: Annotated[str, Depends(oauth2_scheme)]) -> User:
    user_id = _decode_token(token, "access")
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(role: Role):
    def checker(user: CurrentUser) -> User:
        if user.role != role:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Only {role.value}s can do this")
        return user

    return checker


SellerUser = Annotated[User, Depends(require_role(Role.SELLER))]
CollectorUser = Annotated[User, Depends(require_role(Role.COLLECTOR))]

