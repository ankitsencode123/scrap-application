"""Users and profile."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.v1.reviews import _reviews_page
from app.core.cursor_pagination import CursorPage, CursorQuery, LimitQuery
from app.core.security import CurrentUser
from app.core.shard_router import get_router
from app.db.base import DB
from app.db.enums import ACTIVE_PICKUP_STATUSES, BidStatus, ListingStatus, Role
from app.db.models import Bid, Listing, User
from app.schemas import (
    Page,
    ProfileUpdate,
    PublicProfile,
    ReviewOut,
    StatsOut,
    UserOut,
    user_brief,
)
from app.services.common import completed_count

users_router = APIRouter(prefix="/users", tags=["Users & Profile"])


# ------------------------------------------------------- USERS / PROFILE ---
# NOTE: "/me" routes are declared before "/{user_id}" so "me" is never parsed as a UUID.
@users_router.get("/me", response_model=UserOut)
def get_me(user: CurrentUser):
    return user


@users_router.patch("/me", response_model=UserOut)
def update_me(payload: ProfileUpdate, db: DB, user: CurrentUser):
    u = db.for_id(user.id).get(User, user.id)
    if not u:
        raise HTTPException(404, "User not found")
    data = payload.model_dump(exclude_unset=True)
    if data.get("display_name") is not None:
        u.display_name = data["display_name"].strip()
    for field in ("phone", "city", "avatar_url"):
        if field in data:
            val = data[field]
            setattr(u, field, (val.strip() or None) if isinstance(val, str) else val)
    if "latitude" in data or "longitude" in data:
        if data.get("latitude") is None or data.get("longitude") is None:
            raise HTTPException(422, "latitude and longitude must be provided together")
        u.latitude, u.longitude = data["latitude"], data["longitude"]
    db.for_id(user.id).commit()
    return u


@users_router.get("/me/stats", response_model=StatsOut)
def my_stats(db: DB, user: CurrentUser):
    """Dashboard: total pickups completed + money earned/paid + real recycled kg + monthly history."""
    from datetime import datetime, timezone
    from decimal import Decimal
    from app.schemas import CategoryWeight, MonthlyBreakdown

    router = get_router()
    col = Listing.seller_id if user.role == Role.SELLER else Listing.collector_id

    # Compute last 7 months window
    now = datetime.now(timezone.utc)
    months_window = []
    for i in range(6, -1, -1):
        idx = now.month - i - 1
        m = (idx % 12) + 1
        y = now.year + (idx // 12)
        month_label = datetime(y, m, 1).strftime("%b")
        months_window.append((y, m, month_label))

    def fetch_shard_stats(s_db, sid: int):
        done_cnt, sum_amt, sum_kg = s_db.execute(
            select(
                func.count(Listing.id),
                func.coalesce(func.sum(Listing.final_price), 0),
                func.coalesce(func.sum(Listing.weight_kg), 0),
            ).where(col == user.id, Listing.status == ListingStatus.COMPLETED)
        ).one()
        act = s_db.scalar(select(func.count(Listing.id)).where(col == user.id, Listing.status.in_(ACTIVE_PICKUP_STATUSES))) or 0

        addrs: list[str] = []
        if user.role == Role.SELLER:
            open_cnt = s_db.scalar(select(func.count(Listing.id)).where(Listing.seller_id == user.id, Listing.status == ListingStatus.POSTED)) or 0
            raw_addrs = s_db.execute(select(Listing.address).where(Listing.seller_id == user.id).distinct().limit(5)).scalars().all()
            addrs = [a.strip() for a in raw_addrs if a and a.strip()]
        else:
            open_cnt = s_db.scalar(select(func.count(Bid.id)).where(Bid.collector_id == user.id, Bid.status == BidStatus.PENDING)) or 0

        # Query completed listings for monthly trend
        completed_rows = s_db.execute(
            select(
                Listing.completed_at,
                Listing.created_at,
                func.coalesce(Listing.final_price, Listing.expected_price, 0),
            ).where(col == user.id, Listing.status == ListingStatus.COMPLETED)
        ).all()

        # Collector van inventory breakdown by category
        van_items = []
        if user.role == Role.COLLECTOR:
            van_rows = s_db.execute(
                select(
                    Listing.category,
                    func.coalesce(func.sum(Listing.weight_kg), 0)
                ).where(Listing.collector_id == user.id, Listing.status == ListingStatus.COMPLETED)
                .group_by(Listing.category)
            ).all()
            van_items = [(cat.value if hasattr(cat, "value") else str(cat), float(kg)) for cat, kg in van_rows]

        return done_cnt, sum_amt, sum_kg, act, open_cnt, addrs, completed_rows, van_items

    res = router.scatter(fetch_shard_stats)
    done = sum(d for d, _, _, _, _, _, _, _ in res.values())
    total = sum(t for _, t, _, _, _, _, _, _ in res.values())
    recycled_kg = float(sum(k for _, _, k, _, _, _, _, _ in res.values()))
    active = sum(a for _, _, _, a, _, _, _, _ in res.values())
    open_items = sum(o for _, _, _, _, o, _, _, _ in res.values())

    # Aggregate saved addresses
    all_addrs: list[str] = []
    seen_addrs = set()
    if user.city:
        city_addr = f"Home · {user.city}"
        all_addrs.append(city_addr)
        seen_addrs.add(city_addr)
    for _, _, _, _, _, shard_addrs, _, _ in res.values():
        for a in shard_addrs:
            if a not in seen_addrs:
                all_addrs.append(a)
                seen_addrs.add(a)

    # Monthly breakdown aggregation
    monthly_sums: dict[tuple[int, int], float] = {(y, m): 0.0 for y, m, _ in months_window}
    for _, _, _, _, _, _, comp_rows, _ in res.values():
        for comp_at, created_at, amt in comp_rows:
            dt = comp_at or created_at
            if dt:
                key = (dt.year, dt.month)
                if key in monthly_sums:
                    monthly_sums[key] += float(amt)

    monthly_earnings = [
        MonthlyBreakdown(month=lbl, amount=round(monthly_sums.get((y, m), 0.0), 2))
        for y, m, lbl in months_window
    ]

    # Van inventory aggregation
    van_cat_map: dict[str, float] = {}
    for _, _, _, _, _, _, _, van_items in res.values():
        for c, kg in van_items:
            van_cat_map[c] = van_cat_map.get(c, 0.0) + kg
    van_inventory = [
        CategoryWeight(category=c, weight_kg=round(kg, 2))
        for c, kg in sorted(van_cat_map.items())
    ]

    co2_saved = round(recycled_kg * 2.2, 1)

    return StatsOut(
        role=user.role,
        completed_pickups=done,
        total_amount=total,
        amount_label="earned" if user.role == Role.SELLER else "paid",
        active_pickups=active,
        open_items=open_items,
        rating_avg=round(user.rating_avg or 0, 2),
        rating_count=user.rating_count or 0,
        recycled_kg=round(recycled_kg, 2),
        co2_saved_kg=co2_saved,
        wallet_balance=total if user.role == Role.SELLER else Decimal("0.00"),
        monthly_earnings=monthly_earnings,
        saved_addresses=all_addrs,
        van_inventory=van_inventory,
    )


@users_router.get("/me/reviews", response_model=CursorPage[ReviewOut])
def my_reviews(
    db: DB,
    user: CurrentUser,
    limit: LimitQuery = 20,
    cursor: CursorQuery = None,
    include_total: Annotated[bool, Query(description="Calculate total count on first page")] = True,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return _reviews_page(db, user.id, limit=limit, cursor=cursor, include_total=include_total, offset=offset)


@users_router.get("/{user_id}", response_model=PublicProfile)
def get_public_profile(user_id: uuid.UUID, db: DB, _: CurrentUser):
    u = db.get(User, user_id)
    if not u or not u.is_active:
        raise HTTPException(404, "User not found")
    brief = user_brief(u)
    return PublicProfile(**brief.model_dump(), role=u.role, member_since=u.created_at, completed_pickups=completed_count(db, u))


@users_router.get("/{user_id}/reviews", response_model=CursorPage[ReviewOut])
def get_user_reviews(
    user_id: uuid.UUID,
    db: DB,
    _: CurrentUser,
    limit: LimitQuery = 20,
    cursor: CursorQuery = None,
    include_total: Annotated[bool, Query(description="Calculate total count on first page")] = True,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    if not db.get(User, user_id):
        raise HTTPException(404, "User not found")
    return _reviews_page(db, user_id, limit=limit, cursor=cursor, include_total=include_total, offset=offset)



