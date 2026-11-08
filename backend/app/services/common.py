"""Section 8: helpers (geo, locking, serialisation)."""

import math
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import Float, func, literal, null, select
from sqlalchemy.orm import Session

from app.core.config import EARTH_RADIUS_KM, settings
from app.core.storage import storage
from app.db.enums import (
    ACTIVE_PICKUP_STATUSES,
    CATEGORY_LABELS,
    PICKUP_FLOW,
    STEP_LABELS,
    BidStatus,
    ListingStatus,
    Role,
)
from app.db.models import Bid, Listing, PickupEvent, Review, User
from app.schemas import (
    BidOut,
    BidSummary,
    ImageOut,
    ListingOut,
    ReviewOut,
    TrackerOut,
    TrackerStep,
    user_brief,
)


def distance_expr(lat: float, lng: float):
    """Haversine distance (km) from (lat,lng) to Listing's coordinates, as a SQL expression."""
    lat_r, lng_r = math.radians(lat), math.radians(lng)
    l_lat, l_lng = func.radians(Listing.latitude), func.radians(Listing.longitude)
    a = func.power(func.sin((l_lat - lat_r) / 2), 2) + math.cos(lat_r) * func.cos(l_lat) * func.power(
        func.sin((l_lng - lng_r) / 2), 2
    )
    return 2 * EARTH_RADIUS_KM * func.asin(func.least(literal(1.0, Float), func.sqrt(a)))


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def lock_listing(db: Session, listing_id: uuid.UUID) -> Listing:
    """SELECT ... FOR UPDATE so concurrent accepts / advances can't race."""
    session = db.for_id(listing_id) if hasattr(db, "for_id") else db
    listing = session.scalar(
        select(Listing)
        .where(Listing.id == listing_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not listing:
        raise HTTPException(404, "Listing not found")
    return listing


def add_event(listing: Listing, new_status: ListingStatus, actor_id: uuid.UUID | None) -> None:
    from app.core.shard_router import get_router
    event_id = get_router().colocated_id(listing.id)
    listing.events.append(PickupEvent(id=event_id, status=new_status, actor_id=actor_id))


def step_of(s: ListingStatus) -> int:
    return PICKUP_FLOW.index(s) + 1 if s in PICKUP_FLOW else 0


def mask_address(address: str, seller_city: str | None) -> str:
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) >= 3:
        return ", ".join(parts[-2:])
    if len(parts) == 2:
        return parts[-1]
    return seller_city or "Exact address shared after your offer is accepted"


def resolve_coords(viewer: User, lat: float | None, lng: float | None):
    if lat is not None and lng is not None:
        return lat, lng
    if viewer.latitude is not None and viewer.longitude is not None:
        return viewer.latitude, viewer.longitude
    return None, None


def listing_select(lat: float | None, lng: float | None):
    """SELECT Listing + distance + active bid count in a single query."""
    dist = distance_expr(lat, lng) if lat is not None and lng is not None else null()
    bid_count = (
        select(func.count(Bid.id))
        .where(Bid.listing_id == Listing.id, Bid.status.in_([BidStatus.PENDING, BidStatus.ACCEPTED]))
        .correlate(Listing)
        .scalar_subquery()
    )
    return select(Listing, dist.label("distance_km"), bid_count.label("bid_count")), dist


def viewer_context(db: Session, viewer: User, listing_ids: list[uuid.UUID]):
    my_bids: dict[uuid.UUID, Bid] = {}
    reviewed: set[uuid.UUID] = set()
    if listing_ids:
        for b in db.scalars(
            select(Bid).where(
                Bid.collector_id == viewer.id,
                Bid.listing_id.in_(listing_ids),
                Bid.status != BidStatus.WITHDRAWN,
            )
        ):
            my_bids[b.listing_id] = b
        reviewed = set(
            db.scalars(select(Review.listing_id).where(Review.reviewer_id == viewer.id, Review.listing_id.in_(listing_ids)))
        )
    return my_bids, reviewed


def viewer_context_sharded(shards, viewer: User, listings: list[Listing]):
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor
    from app.core.shard_router import shard_id_of

    my_bids: dict[uuid.UUID, Bid] = {}
    reviewed: set[uuid.UUID] = set()
    if not listings:
        return my_bids, reviewed
    by_shard: dict[int, list[uuid.UUID]] = defaultdict(list)
    for l in listings:
        by_shard[shard_id_of(l.id)].append(l.id)

    def _fetch_shard_context(item: tuple[int, list[uuid.UUID]]):
        sid, l_ids = item
        s_db = shards.for_shard(sid)
        bids = list(
            s_db.scalars(
                select(Bid).where(
                    Bid.collector_id == viewer.id,
                    Bid.listing_id.in_(l_ids),
                    Bid.status != BidStatus.WITHDRAWN,
                )
            )
        )
        revs = list(
            s_db.scalars(
                select(Review.listing_id).where(Review.reviewer_id == viewer.id, Review.listing_id.in_(l_ids))
            )
        )
        return bids, revs

    # Execute independent shard queries in parallel
    if len(by_shard) <= 1:
        for sid, l_ids in by_shard.items():
            bids, revs = _fetch_shard_context((sid, l_ids))
            for b in bids:
                my_bids[b.listing_id] = b
            for r_lid in revs:
                reviewed.add(r_lid)
    else:
        with ThreadPoolExecutor(max_workers=min(len(by_shard), 8)) as ex:
            results = ex.map(_fetch_shard_context, by_shard.items())
            for bids, revs in results:
                for b in bids:
                    my_bids[b.listing_id] = b
                for r_lid in revs:
                    reviewed.add(r_lid)

    return my_bids, reviewed


def _coord(value: float | None, exact: bool) -> float | None:
    """Exact coordinates for the two parties; ~1 km approximate pins for everyone else (so the map works
    without revealing the doorstep)."""
    if value is None:
        return None
    return value if exact else round(value, 2)


def listing_out(
    l: Listing,
    viewer: User,
    distance: float | None,
    bid_count: int,
    my_bid: Bid | None,
    has_reviewed: bool,
    seller: User | None = None,
    collector: User | None = None,
) -> ListingOut:
    is_owner = l.seller_id == viewer.id
    is_collector = l.collector_id == viewer.id
    is_party = is_owner or is_collector
    seller_city = seller.city if seller else None
    address = (
        l.address
        if (is_party or not settings.MASK_ADDRESS_FOR_PUBLIC)
        else mask_address(l.address, seller_city)
    )
    return ListingOut(
        id=l.id,
        title=l.title,
        category=l.category,
        category_label=CATEGORY_LABELS[l.category],
        weight_kg=l.weight_kg,
        expected_price=l.expected_price,
        address=address,
        description=l.description,
        latitude=_coord(l.latitude, exact=is_party or not settings.MASK_ADDRESS_FOR_PUBLIC),
        longitude=_coord(l.longitude, exact=is_party or not settings.MASK_ADDRESS_FOR_PUBLIC),
        distance_km=round(distance, 1) if distance is not None else None,
        status=l.status,
        current_step=step_of(l.status),
        images=[ImageOut(id=i.id, url=i.url) for i in l.images],
        seller=user_brief(seller, include_phone=is_collector) if seller else None,
        collector=user_brief(collector, include_phone=is_owner) if collector else None,
        bid_count=bid_count or 0,
        my_bid=BidSummary(id=my_bid.id, amount=my_bid.amount, note=my_bid.note, status=my_bid.status) if my_bid else None,
        final_price=l.final_price,
        is_owner=is_owner,
        is_assigned_collector=is_collector,
        can_review=l.status == ListingStatus.COMPLETED and is_party and not has_reviewed,
        has_reviewed=has_reviewed,
        pickup_time_slot=getattr(l, "pickup_time_slot", None),
        floor_access=getattr(l, "floor_access", None),
        is_negotiable=getattr(l, "is_negotiable", True) if getattr(l, "is_negotiable", None) is not None else True,
        packaging_type=getattr(l, "packaging_type", None),
        created_at=l.created_at,
        accepted_at=l.accepted_at,
        completed_at=l.completed_at,
    )


def rows_to_out(shards, viewer: User, rows) -> list[ListingOut]:
    if not rows:
        return []
    listings = [r[0] for r in rows]
    if hasattr(shards, "for_shard"):
        my_bids, reviewed = viewer_context_sharded(shards, viewer, listings)
    else:
        my_bids, reviewed = viewer_context(shards, viewer, [l.id for l in listings])

    user_ids = {l.seller_id for l in listings} | {l.collector_id for l in listings if l.collector_id}
    from app.core.shard_router import get_router
    router = get_router()
    users = router.load_by_ids(User, user_ids)
    if viewer.id in user_ids:
        users[viewer.id] = viewer

    return [
        listing_out(
            l,
            viewer,
            dist,
            count,
            my_bids.get(l.id),
            l.id in reviewed,
            seller=users.get(l.seller_id),
            collector=users.get(l.collector_id) if l.collector_id else None,
        )
        for l, dist, count in rows
    ]


def fetch_listing_out(db, viewer: User, listing_id: uuid.UUID, lat=None, lng=None) -> ListingOut:
    lat, lng = resolve_coords(viewer, lat, lng)
    sel, _ = listing_select(lat, lng)
    session = db.for_id(listing_id) if hasattr(db, "for_id") else db
    row = session.execute(sel.where(Listing.id == listing_id).execution_options(populate_existing=True)).first()
    if not row:
        raise HTTPException(404, "Listing not found")
    l = row[0]
    if l.status != ListingStatus.POSTED and viewer.id not in (l.seller_id, l.collector_id):
        raise HTTPException(404, "Listing not found")

    user_ids = {uid for uid in (l.seller_id, l.collector_id) if uid is not None}
    users: dict[uuid.UUID, User] = {viewer.id: viewer} if viewer.id in user_ids else {}
    needed = user_ids - set(users.keys())
    if needed:
        from app.core.shard_router import get_router
        users.update(get_router().load_by_ids(User, needed))

    my_bids, reviewed = viewer_context(session, viewer, [l.id])
    return listing_out(
        l,
        viewer,
        distance=row[1],
        bid_count=row[2],
        my_bid=my_bids.get(l.id),
        has_reviewed=l.id in reviewed,
        seller=users.get(l.seller_id),
        collector=users.get(l.collector_id) if l.collector_id else None,
    )


def validate_image_urls(urls: list[str], user: User) -> list[tuple[str, str]]:
    """Only accept images that *this user* uploaded through our own /uploads endpoint."""
    seen: dict[str, str] = {}
    for url in urls:
        key = storage.key_from_url(url)
        if not key or not key.startswith(f"listings/{user.id}/"):
            raise HTTPException(400, "Image URL not recognised: upload it via /uploads/images first")
        seen[url] = key
    if len(seen) > settings.MAX_LISTING_IMAGES:
        raise HTTPException(400, f"At most {settings.MAX_LISTING_IMAGES} images per listing")
    if len(seen) < settings.MIN_LISTING_IMAGES:
        raise HTTPException(422, f"Add at least {settings.MIN_LISTING_IMAGES} photo(s) of your scrap")
    return list(seen.items())


def build_tracker(
    l: Listing,
    viewer: User,
    seller: User | None = None,
    collector: User | None = None,
) -> TrackerOut:
    reached: dict[ListingStatus, datetime] = {}
    for e in l.events:
        reached.setdefault(e.status, e.created_at)
    reached.setdefault(ListingStatus.POSTED, l.created_at)

    cancelled = l.status == ListingStatus.CANCELLED
    if cancelled:
        current = max((step_of(s) for s in reached if s in PICKUP_FLOW), default=1)
    else:
        current = step_of(l.status)

    steps = [
        TrackerStep(
            step=i,
            status=s,
            label=STEP_LABELS[s],
            done=i <= current,
            current=i == current,
            timestamp=reached.get(s),
        )
        for i, s in enumerate(PICKUP_FLOW, start=1)
    ]
    is_party = viewer.id in (l.seller_id, l.collector_id)
    next_status = None
    if not cancelled and l.status in ACTIVE_PICKUP_STATUSES:
        next_status = PICKUP_FLOW[current]  # index == current -> next step
    return TrackerOut(
        listing_id=l.id,
        status=l.status,
        current_step=current,
        total_steps=len(PICKUP_FLOW),
        cancelled=cancelled,
        steps=steps,
        next_status=next_status,
        next_label=STEP_LABELS[next_status] if next_status else None,
        can_advance=bool(next_status and is_party),
        final_price=l.final_price,
        seller=user_brief(seller, include_phone=viewer.id == l.collector_id) if seller else None,
        collector=user_brief(collector, include_phone=viewer.id == l.seller_id) if collector else None,
    )


def bid_out(b: Bid, is_highest: bool = False, reveal_phone: bool = False, collector: User | None = None) -> BidOut:
    return BidOut(
        id=b.id,
        listing_id=b.listing_id,
        amount=b.amount,
        note=b.note,
        status=b.status,
        is_highest=is_highest,
        created_at=b.created_at,
        collector=user_brief(collector, include_phone=reveal_phone and b.status == BidStatus.ACCEPTED) if collector else None,
    )


def review_out(r: Review, reviewer: User | None = None) -> ReviewOut:
    return ReviewOut(
        id=r.id,
        rating=r.rating,
        comment=r.comment,
        created_at=r.created_at,
        listing_id=r.listing_id,
        listing_title=r.listing.title if r.listing else "",
        reviewer=user_brief(reviewer) if reviewer else None,
        reviewer_role=reviewer.role if reviewer else Role.SELLER,
    )


def completed_count(db, user: User) -> int:
    from app.core.shard_router import get_router
    col = Listing.seller_id if user.role == Role.SELLER else Listing.collector_id
    router = get_router()
    res = router.scatter(lambda session, sid: session.scalar(select(func.count(Listing.id)).where(col == user.id, Listing.status == ListingStatus.COMPLETED)) or 0)
    return sum(res.values())


