"""Aggregates every v1 router under API_PREFIX (default /api/v1)."""

from fastapi import APIRouter

from app.api.v1.auth import auth_router
from app.api.v1.bids import bids_router
from app.api.v1.chat_ws import chat_router
from app.api.v1.listings import listings_router
from app.api.v1.map_system import map_router
from app.api.v1.messages import messages_router
from app.api.v1.meta import meta_router
from app.api.v1.pickup import pickup_router
from app.api.v1.reviews import reviews_router
from app.api.v1.uploads import uploads_router
from app.api.v1.users import users_router
from app.core.config import settings

api = APIRouter(prefix=settings.API_PREFIX)

for r in (
    meta_router,
    auth_router,
    users_router,
    uploads_router,
    listings_router,
    bids_router,
    pickup_router,
    reviews_router,
    messages_router,
    chat_router,
    map_router,
):
    api.include_router(r)
