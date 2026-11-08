"""Pickup tracker."""

import uuid

from fastapi import APIRouter, HTTPException

from app.api.v1.map_system import emit_listing_event
from app.core.security import CurrentUser
from app.core.shard_router import get_router
from app.db.base import DB, utcnow
from app.db.enums import ACTIVE_PICKUP_STATUSES, PICKUP_FLOW, ListingStatus
from app.db.models import Listing, User
from app.schemas import AdvanceIn, TrackerOut
from app.services.common import add_event, build_tracker, lock_listing, step_of

pickup_router = APIRouter(prefix="/listings", tags=["Pickup Tracker"])


# ---------------------------------------------------------- PICKUP TRACKER ---
@pickup_router.get("/{listing_id}/tracker", response_model=TrackerOut)
def get_tracker(listing_id: uuid.UUID, db: DB, user: CurrentUser):
    s_db = db.for_id(listing_id)
    listing = s_db.get(Listing, listing_id)
    if not listing or user.id not in (listing.seller_id, listing.collector_id):
        raise HTTPException(404, "Listing not found")
    user_ids = {listing.seller_id, listing.collector_id} - {None}
    users = {user.id: user} if user.id in user_ids else {}
    needed = user_ids - set(users.keys())
    if needed:
        users.update(get_router().load_by_ids(User, needed))
    return build_tracker(
        listing,
        user,
        seller=users.get(listing.seller_id),
        collector=users.get(listing.collector_id) if listing.collector_id else None,
    )


@pickup_router.post("/{listing_id}/advance", response_model=TrackerOut)
def advance_pickup(listing_id: uuid.UUID, db: DB, user: CurrentUser, payload: AdvanceIn | None = None):
    """
    Move the pickup one step forward:
      offer_accepted -> on_the_way -> arrived -> completed (picked up & paid)
    Either the seller or the assigned collector may advance it. Steps cannot be skipped
    or reversed. (Step 2 happens by accepting a bid.)
    """
    s_db = db.for_id(listing_id)
    listing = lock_listing(s_db, listing_id)
    if user.id not in (listing.seller_id, listing.collector_id):
        raise HTTPException(404, "Listing not found")
    if payload and payload.expected_status and payload.expected_status != listing.status:
        raise HTTPException(409, f"Status already changed to '{listing.status.value}'. Refresh and retry.")
    if listing.status == ListingStatus.POSTED:
        raise HTTPException(409, "Accept an offer before starting the pickup")
    if listing.status not in ACTIVE_PICKUP_STATUSES:
        raise HTTPException(409, f"Pickup is already {listing.status.value}")

    nxt = PICKUP_FLOW[step_of(listing.status)]
    listing.status = nxt
    if nxt == ListingStatus.COMPLETED:
        listing.completed_at = utcnow()
    add_event(listing, nxt, user.id)
    s_db.commit()
    s_db.refresh(listing)
    emit_listing_event("listing.status", listing)

    user_ids = {listing.seller_id, listing.collector_id} - {None}
    users = {user.id: user} if user.id in user_ids else {}
    needed = user_ids - set(users.keys())
    if needed:
        users.update(get_router().load_by_ids(User, needed))
    return build_tracker(
        listing,
        user,
        seller=users.get(listing.seller_id),
        collector=users.get(listing.collector_id) if listing.collector_id else None,
    )



