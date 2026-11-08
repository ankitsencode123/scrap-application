"""Auth endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import or_, select

from app.core.config import log, settings
from app.core.security import (
    _DUMMY_HASH,
    CurrentUser,
    _decode_token,
    build_token_response,
    hash_password,
    verify_password,
)
from app.core.shard_router import NS_EMAIL, NS_GOOGLE, get_router
from app.db.base import DB
from app.db.models import User
from app.schemas import (
    ChangePasswordIn,
    GoogleAuthIn,
    LoginIn,
    RefreshIn,
    SignupIn,
    TokenOut,
)

auth_router = APIRouter(prefix="/auth", tags=["Auth"])


# ------------------------------------------------------------------- AUTH ---
@auth_router.post("/signup", response_model=TokenOut, status_code=201)
def signup(payload: SignupIn, db: DB):
    email = payload.email.strip().lower()
    router = get_router()
    alloc = router.allocate()
    if not router.directory.reserve(NS_EMAIL, email, alloc.entity_id):
        raise HTTPException(409, "An account with this email already exists")

    try:
        user = User(
            id=alloc.entity_id,
            email=email,
            password_hash=hash_password(payload.password),
            role=payload.role,
            display_name=payload.display_name,
            phone=payload.phone,
            city=payload.city,
        )
        s_db = db.for_shard(alloc.shard_id)
        s_db.add(user)
        s_db.commit()
    except Exception:
        router.directory.release(NS_EMAIL, email, alloc.entity_id)
        raise HTTPException(409, "An account with this email already exists")

    return build_token_response(user, is_new_user=True)


def _authenticate(db: DB, email: str, password: str) -> User:
    router = get_router()
    email_clean = email.strip().lower()
    uid = router.directory.lookup(NS_EMAIL, email_clean)
    user = None
    if uid:
        user = db.for_id(uid).get(User, uid)
    else:
        # Fallback to default shard for legacy seeded records
        user = db.default.scalar(select(User).where(User.email == email_clean))
        if user:
            router.directory.reserve(NS_EMAIL, email_clean, user.id)

    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return user


@auth_router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, db: DB):
    return build_token_response(_authenticate(db, payload.email, payload.password))


@auth_router.post("/token", response_model=TokenOut, include_in_schema=True)
def login_form(db: DB, form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    """OAuth2 form login - lets the Swagger UI 'Authorize' button work (username = email)."""
    return build_token_response(_authenticate(db, form.username, form.password))


@auth_router.post("/google", response_model=TokenOut)
def google_auth(payload: GoogleAuthIn, db: DB):
    """
    Frontend sends the Google ID token (credential). If the account does not exist yet,
    `role` is required - otherwise we answer 400 {code: "ROLE_REQUIRED"} so the UI can
    show the Seller/Collector chooser and call this endpoint again.
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(501, "Google sign-in is not configured")
    try:
        from google.auth.exceptions import GoogleAuthError
        from google.auth.transport import requests as g_requests
        from google.oauth2 import id_token as g_id_token
    except ImportError:
        raise HTTPException(501, "google-auth is not installed on the server")
    try:
        info = g_id_token.verify_oauth2_token(payload.id_token, g_requests.Request(), settings.GOOGLE_CLIENT_ID)
    except (ValueError, GoogleAuthError) as e:
        log.warning("Google token verification failed: %s", e)
        raise HTTPException(401, "Invalid Google token")
    if not info.get("email_verified"):
        raise HTTPException(401, "Google email is not verified")

    email, sub = info["email"].lower(), str(info["sub"])
    router = get_router()
    uid = router.directory.lookup(NS_GOOGLE, sub) or router.directory.lookup(NS_EMAIL, email)
    user = None
    if uid:
        user = db.for_id(uid).get(User, uid)
    else:
        user = db.default.scalar(select(User).where(or_(User.google_sub == sub, User.email == email)))
        if user:
            router.directory.reserve(NS_GOOGLE, sub, user.id)
            router.directory.reserve(NS_EMAIL, email, user.id)

    if user:
        if not user.is_active:
            raise HTTPException(401, "Account is disabled")
        if not user.google_sub:
            user.google_sub = sub
            router.directory.reserve(NS_GOOGLE, sub, user.id)
        if not user.avatar_url and info.get("picture"):
            user.avatar_url = info["picture"]
        db.for_id(user.id).commit()
        return build_token_response(user)

    if payload.role is None:
        raise HTTPException(400, {"code": "ROLE_REQUIRED", "message": "Choose Seller or Collector to finish signing up"})

    alloc = router.allocate()
    if not router.directory.reserve(NS_EMAIL, email, alloc.entity_id):
        raise HTTPException(409, "An account with this email already exists")
    router.directory.reserve(NS_GOOGLE, sub, alloc.entity_id)

    try:
        user = User(
            id=alloc.entity_id,
            email=email,
            google_sub=sub,
            role=payload.role,
            display_name=(info.get("name") or email.split("@")[0])[:80],
            avatar_url=info.get("picture"),
        )
        s_db = db.for_shard(alloc.shard_id)
        s_db.add(user)
        s_db.commit()
    except Exception:
        router.directory.release(NS_EMAIL, email, alloc.entity_id)
        router.directory.release(NS_GOOGLE, sub, alloc.entity_id)
        raise HTTPException(409, "Account creation failed")

    return build_token_response(user, is_new_user=True)


@auth_router.post("/refresh", response_model=TokenOut)
def refresh(payload: RefreshIn, db: DB):
    user = db.get(User, _decode_token(payload.refresh_token, "refresh"))
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")
    return build_token_response(user)


@auth_router.post("/change-password", status_code=204)
def change_password(payload: ChangePasswordIn, db: DB, user: CurrentUser):
    u = db.for_id(user.id).get(User, user.id)
    if not u:
        raise HTTPException(404, "User not found")
    if u.password_hash and not verify_password(payload.current_password or "", u.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    u.password_hash = hash_password(payload.new_password)
    db.for_id(user.id).commit()
    return Response(status_code=204)

