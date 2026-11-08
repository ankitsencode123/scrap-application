"""Listings and feed."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select, update

from app.core.cursor_pagination import (
    CursorPage,
    CursorQuery,
    LimitQuery,
    SortColumn,
    SortSpec,
    DATETIME,
    DECIMAL,
    UUID_,
    FLOAT,
    make_fingerprint,
    paginate_sharded,
)
from app.api.v1.map_system import emit_listing_event
from app.core.security import CurrentUser, SellerUser
from app.core.shard_router import get_router, merge_page
from app.core.storage import storage
from app.db.base import DB
from app.db.enums import BidStatus, Category, ListingStatus, Role
from app.db.models import Bid, Listing, ListingImage, PickupEvent
from app.schemas import ListingCreate, ListingOut, ListingUpdate, Page
from app.services.common import (
    _escape_like,
    add_event,
    fetch_listing_out,
    listing_select,
    lock_listing,
    resolve_coords,
    rows_to_out,
    validate_image_urls,
)

listings_router = APIRouter(prefix="/listings", tags=["Listings & Feed"])


def _sorts(dist):
    ident = lambda desc: SortColumn("id", Listing.id, UUID_, lambda r: r[0].id, descending=desc)
    created = SortColumn("created_at", Listing.created_at, DATETIME, lambda r: r[0].created_at, descending=True)
    price = lambda desc: SortColumn("expected_price", Listing.expected_price, DECIMAL, lambda r: r[0].expected_price, descending=desc)
    weight = SortColumn("weight_kg", Listing.weight_kg, DECIMAL, lambda r: r[0].weight_kg, descending=True)
    near = SortColumn("distance", dist, FLOAT, lambda r: r[1], nullable=True)
    return {
        "newest": SortSpec("newest", [created, ident(True)]),
        "price_high": SortSpec("price_high", [price(True), ident(True)]),
        "price_low": SortSpec("price_low", [price(False), ident(False)]),
        "weight_high": SortSpec("weight_high", [weight, ident(True)]),
        "nearest": SortSpec("nearest", [near, ident(False)]),
    }


# --------------------------------------------------------------- LISTINGS ---
@listings_router.get("", response_model=CursorPage[ListingOut])
def list_listings(
    db: DB,
    user: CurrentUser,
    tab: Annotated[Literal["open", "mine"], Query(description="open = marketplace feed; mine = 'My scrap' (seller) / 'My pickups' (collector)")] = "open",
    category: Category | None = None,
    status_filter: Annotated[ListingStatus | None, Query(alias="status", description="Only for tab=mine")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    min_price: Annotated[float | None, Query(ge=0)] = None,
    max_price: Annotated[float | None, Query(ge=0)] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    max_distance_km: Annotated[float | None, Query(gt=0, le=2000)] = None,
    sort: Literal["newest", "nearest", "price_high", "price_low", "weight_high"] = "newest",
    limit: LimitQuery = 20,
    cursor: CursorQuery = None,
    include_total: Annotated[bool, Query(description="Calculate total count on first page")] = True,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    lat, lng = resolve_coords(user, lat, lng)  # falls back to the profile location
    has_coords = lat is not None and lng is not None
    sel, dist = listing_select(lat, lng)

    filters = []
    if tab == "open":
        filters.append(Listing.status == ListingStatus.POSTED)
    else:
        filters.append(Listing.seller_id == user.id if user.role == Role.SELLER else Listing.collector_id == user.id)
        if status_filter:
            filters.append(Listing.status == status_filter)
    if category:
        filters.append(Listing.category == category)
    if min_price is not None:
        filters.append(Listing.expected_price >= min_price)
    if max_price is not None:
        filters.append(Listing.expected_price <= max_price)
    if q and q.strip():
        like = f"%{_escape_like(q.strip())}%"
        filters.append(or_(Listing.title.ilike(like, escape="\\"), Listing.description.ilike(like, escape="\\")))
    if max_distance_km is not None and has_coords:
        filters.append(Listing.latitude.is_not(None))
        filters.append(dist <= max_distance_km)

    effective = "nearest" if (sort == "nearest" and has_coords) else ("newest" if sort == "nearest" else sort)
    spec = _sorts(dist)[effective]
    router = get_router()

    if offset > 0 and cursor is None:
        orders = {
            "newest": [Listing.created_at.desc(), Listing.id.desc()],
            "price_high": [Listing.expected_price.desc(), Listing.id.desc()],
            "price_low": [Listing.expected_price.asc(), Listing.id.asc()],
            "weight_high": [Listing.weight_kg.desc(), Listing.id.desc()],
        }
        order = [dist.asc().nulls_last(), Listing.id.desc()] if (sort == "nearest" and has_coords) else orders.get(sort, orders["newest"])
        fetch_limit = offset + limit

        def fetch_shard(s_db, sid: int):
            count = s_db.scalar(select(func.count(Listing.id)).where(*filters)) or 0
            rows = s_db.execute(sel.where(*filters).order_by(*order).limit(fetch_limit)).all()
            return count, rows

        res = router.scatter(fetch_shard)
        total = sum(c for c, _ in res.values())
        per_shard_rows = [r for _, r in res.values()]
        import itertools
        import heapq
        window = list(itertools.islice(heapq.merge(*per_shard_rows, key=spec.merge_key), offset, offset + limit))
        return CursorPage[ListingOut](
            items=rows_to_out(db, user, window),
            total=total,
            limit=limit,
            offset=offset,
            has_more=len(window) == limit,
        )

    fp = make_fingerprint(
        tab=tab,
        category=category,
        status=status_filter,
        q=q,
        min=min_price,
        max=max_price,
        lat=lat,
        lng=lng,
        dist=max_distance_km,
        viewer=user.id,
    )
    result = paginate_sharded(
        router,
        base=sel,
        spec=spec,
        filters=filters,
        limit=limit,
        cursor=cursor,
        fingerprint=fp,
        with_total=include_total,
        count_stmt=select(func.count(Listing.id)),
    )
    return CursorPage[ListingOut](items=rows_to_out(db, user, result.rows), **result.meta(limit), offset=0)


@listings_router.post("", response_model=ListingOut, status_code=201)
def create_listing(payload: ListingCreate, db: DB, seller: SellerUser):
    images = validate_image_urls(payload.image_urls, seller)
    lat, lng = payload.latitude, payload.longitude
    if lat is None:  # fall back to profile location so distance works for collectors
        lat, lng = seller.latitude, seller.longitude

    router = get_router()
    alloc = router.allocate(near=seller.id)
    listing_id = alloc.entity_id

    listing = Listing(
        id=listing_id,
        seller_id=seller.id,
        title=payload.title,
        category=payload.category,
        weight_kg=payload.weight_kg,
        expected_price=payload.expected_price,
        address=payload.address,
        description=payload.description,
        latitude=lat,
        longitude=lng,
        status=ListingStatus.POSTED,
        pickup_time_slot=payload.pickup_time_slot,
        floor_access=payload.floor_access,
        is_negotiable=payload.is_negotiable,
        packaging_type=payload.packaging_type,
    )
    listing.images = [
        ListingImage(id=router.colocated_id(listing_id), url=u, storage_key=k, position=i)
        for i, (u, k) in enumerate(images)
    ]
    listing.events = [
        PickupEvent(id=router.colocated_id(listing_id), status=ListingStatus.POSTED, actor_id=seller.id)
    ]
    s_db = db.for_shard(alloc.shard_id)
    s_db.add(listing)
    s_db.commit()
    emit_listing_event("listing.created", listing)
    return fetch_listing_out(db, seller, listing.id)


@listings_router.get("/{listing_id}", response_model=ListingOut)
def get_listing(
    listing_id: uuid.UUID,
    db: DB,
    user: CurrentUser,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
):
    return fetch_listing_out(db, user, listing_id, lat, lng)


@listings_router.patch("/{listing_id}", response_model=ListingOut)
def update_listing(listing_id: uuid.UUID, payload: ListingUpdate, db: DB, seller: SellerUser):
    s_db = db.for_id(listing_id)
    listing = lock_listing(s_db, listing_id)
    if listing.seller_id != seller.id:
        raise HTTPException(403, "Not your listing")
    if listing.status != ListingStatus.POSTED:
        raise HTTPException(409, "Only listings that are still open can be edited")

    data = payload.model_dump(exclude_unset=True)
    image_urls = data.pop("image_urls", None)
    data = {k: v for k, v in data.items() if v is not None or k == "description"}
    if ("latitude" in data) != ("longitude" in data):
        raise HTTPException(422, "latitude and longitude must be provided together")
    for k, v in data.items():
        setattr(listing, k, v)

    removed_keys: list[str] = []
    if image_urls is not None:
        images = validate_image_urls(image_urls, seller)
        keep = {u for u, _ in images}
        removed_keys = [i.storage_key for i in listing.images if i.url not in keep]
        router = get_router()
        listing.images = [
            ListingImage(id=router.colocated_id(listing_id), url=u, storage_key=k, position=i)
            for i, (u, k) in enumerate(images)
        ]
    s_db.commit()
    emit_listing_event("listing.status", listing)
    for key in removed_keys:
        storage.delete(key)
    return fetch_listing_out(db, seller, listing.id)


@listings_router.post("/{listing_id}/cancel", response_model=ListingOut)
def cancel_listing(listing_id: uuid.UUID, db: DB, seller: SellerUser):
    s_db = db.for_id(listing_id)
    listing = lock_listing(s_db, listing_id)
    if listing.seller_id != seller.id:
        raise HTTPException(403, "Not your listing")
    if listing.status not in (ListingStatus.POSTED, ListingStatus.OFFER_ACCEPTED):
        raise HTTPException(409, "This pickup is already in progress and can no longer be cancelled")
    listing.status = ListingStatus.CANCELLED
    s_db.execute(
        update(Bid).where(Bid.listing_id == listing.id, Bid.status == BidStatus.PENDING).values(status=BidStatus.REJECTED)
    )
    add_event(listing, ListingStatus.CANCELLED, seller.id)
    s_db.commit()
    emit_listing_event("listing.status", listing)
    return fetch_listing_out(db, seller, listing.id)



