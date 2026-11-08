"""Bidding."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func, select, update

from app.core.cursor_pagination import (
    CursorPage,
    CursorQuery,
    LimitQuery,
    SortColumn,
    SortSpec,
    DATETIME,
    UUID_,
    make_fingerprint,
    paginate_sharded,
)
from app.api.v1.map_system import emit_listing_event, emit_user_event
from app.core.security import CollectorUser, SellerUser
from app.core.shard_router import get_router, merge_page
from app.db.base import DB, utcnow
from app.db.enums import CATEGORY_LABELS, BidStatus, ListingStatus
from app.db.models import Bid, Listing, User
from app.schemas import BidCreate, BidOut, ListingMini, ListingOut, MyBidOut, Page
from app.services.common import add_event, bid_out, fetch_listing_out, lock_listing

bids_router = APIRouter(tags=["Bids"])


# ------------------------------------------------------------------- BIDS ---
@bids_router.post("/listings/{listing_id}/bids", response_model=BidOut, status_code=201)
def place_bid(listing_id: uuid.UUID, payload: BidCreate, response: Response, db: DB, collector: CollectorUser):
    """Create a bid, or update your existing bid on this listing (one active bid per collector)."""
    s_db = db.for_id(listing_id)
    listing = lock_listing(s_db, listing_id)
    if listing.status != ListingStatus.POSTED:
        raise HTTPException(409, "This listing is no longer accepting offers")
    bid = s_db.scalar(select(Bid).where(Bid.listing_id == listing.id, Bid.collector_id == collector.id))
    if bid:
        bid.amount, bid.note, bid.status = payload.amount, payload.note, BidStatus.PENDING
        response.status_code = 200
    else:
        router = get_router()
        bid = Bid(
            id=router.colocated_id(listing.id),
            listing_id=listing.id,
            collector_id=collector.id,
            amount=payload.amount,
            note=payload.note,
        )
        s_db.add(bid)
    s_db.commit()
    s_db.refresh(bid)
    emit_user_event(listing.seller_id, "bid.placed", {"listing_id": str(listing.id)})
    return bid_out(bid, collector=collector)


@bids_router.get("/listings/{listing_id}/bids", response_model=list[BidOut])
def list_bids(listing_id: uuid.UUID, db: DB, seller: SellerUser):
    """Seller view: every competing bid with collector name + star rating, best offer first."""
    s_db = db.for_id(listing_id)
    listing = s_db.get(Listing, listing_id)
    if not listing or listing.seller_id != seller.id:
        raise HTTPException(404, "Listing not found")
    bids = list(
        s_db.scalars(
            select(Bid)
            .where(Bid.listing_id == listing_id, Bid.status.in_([BidStatus.PENDING, BidStatus.ACCEPTED]))
            .order_by(Bid.amount.desc(), Bid.created_at.asc())
        )
    )
    if not bids:
        return []
    router = get_router()
    collector_ids = {b.collector_id for b in bids}
    users = router.load_by_ids(User, collector_ids)
    top = bids[0].amount
    return [bid_out(b, is_highest=(b.amount == top), reveal_phone=True, collector=users.get(b.collector_id)) for b in bids]


@bids_router.post("/listings/{listing_id}/bids/{bid_id}/accept", response_model=ListingOut)
def accept_bid(listing_id: uuid.UUID, bid_id: uuid.UUID, db: DB, seller: SellerUser):
    s_db = db.for_id(listing_id)
    listing = lock_listing(s_db, listing_id)
    if listing.seller_id != seller.id:
        raise HTTPException(403, "Not your listing")
    if listing.status != ListingStatus.POSTED:
        raise HTTPException(409, "An offer has already been accepted for this listing")
    bid = s_db.scalar(select(Bid).where(Bid.id == bid_id, Bid.listing_id == listing.id).with_for_update())
    if not bid:
        raise HTTPException(404, "Bid not found")
    if bid.status != BidStatus.PENDING:
        raise HTTPException(409, "This bid is no longer available (the collector may have withdrawn it)")

    bid.status = BidStatus.ACCEPTED
    s_db.execute(
        update(Bid)
        .where(Bid.listing_id == listing.id, Bid.id != bid.id, Bid.status == BidStatus.PENDING)
        .values(status=BidStatus.REJECTED)
    )
    listing.status = ListingStatus.OFFER_ACCEPTED
    listing.collector_id = bid.collector_id
    listing.final_price = bid.amount
    listing.accepted_at = utcnow()
    add_event(listing, ListingStatus.OFFER_ACCEPTED, seller.id)
    s_db.commit()
    emit_listing_event("listing.status", listing)
    return fetch_listing_out(db, seller, listing.id)


@bids_router.delete("/bids/{bid_id}", status_code=204)
def withdraw_bid(bid_id: uuid.UUID, db: DB, collector: CollectorUser):
    s_db = db.for_id(bid_id)
    bid0 = s_db.get(Bid, bid_id)
    if not bid0 or bid0.collector_id != collector.id:
        raise HTTPException(404, "Bid not found")
    lock_listing(s_db, bid0.listing_id)  # same lock order as accept_bid -> no deadlocks
    bid = s_db.scalar(select(Bid).where(Bid.id == bid_id).with_for_update().execution_options(populate_existing=True))
    if bid.status != BidStatus.PENDING:
        raise HTTPException(409, "Only pending bids can be withdrawn")
    bid.status = BidStatus.WITHDRAWN
    s_db.commit()
    return Response(status_code=204)


@bids_router.get("/bids/mine", response_model=CursorPage[MyBidOut])
def my_bids(
    db: DB,
    collector: CollectorUser,
    status_filter: Annotated[BidStatus | None, Query(alias="status")] = None,
    limit: LimitQuery = 20,
    cursor: CursorQuery = None,
    include_total: Annotated[bool, Query(description="Calculate total count on first page")] = True,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    filters = [Bid.collector_id == collector.id, Bid.status != BidStatus.WITHDRAWN]
    if status_filter:
        filters = [Bid.collector_id == collector.id, Bid.status == status_filter]

    router = get_router()
    ident = lambda desc: SortColumn("id", Bid.id, UUID_, lambda r: r[0].id, descending=desc)
    created = SortColumn("created_at", Bid.created_at, DATETIME, lambda r: r[0].created_at, descending=True)
    spec = SortSpec("newest", [created, ident(True)])

    if offset > 0 and cursor is None:
        fetch_limit = offset + limit
        res = router.scatter(
            lambda s_db, sid: (
                s_db.scalar(select(func.count(Bid.id)).where(*filters)) or 0,
                list(s_db.scalars(select(Bid).where(*filters).order_by(Bid.created_at.desc(), Bid.id.desc()).limit(fetch_limit))),
            )
        )
        total = sum(c for c, _ in res.values())
        per_shard_bids = [b for _, b in res.values()]
        bids = merge_page(per_shard_bids, key=lambda b: b.created_at.timestamp(), limit=limit, offset=offset, reverse=True)
        items = [
            MyBidOut(
                id=b.id,
                amount=b.amount,
                note=b.note,
                status=b.status,
                created_at=b.created_at,
                listing=ListingMini(
                    id=b.listing.id,
                    title=b.listing.title,
                    category=b.listing.category,
                    category_label=CATEGORY_LABELS[b.listing.category],
                    expected_price=b.listing.expected_price,
                    status=b.listing.status,
                    cover_image=b.listing.images[0].url if b.listing.images else None,
                ),
            )
            for b in bids
        ]
        return CursorPage[MyBidOut](items=items, total=total, limit=limit, offset=offset, has_more=len(bids) == limit)

    fp = make_fingerprint(collector=collector.id, status=status_filter)
    result = paginate_sharded(
        router,
        base=select(Bid),
        spec=spec,
        filters=filters,
        limit=limit,
        cursor=cursor,
        fingerprint=fp,
        with_total=include_total,
        count_stmt=select(func.count(Bid.id)),
    )
    bids = [r[0] for r in result.rows]
    items = [
        MyBidOut(
            id=b.id,
            amount=b.amount,
            note=b.note,
            status=b.status,
            created_at=b.created_at,
            listing=ListingMini(
                id=b.listing.id,
                title=b.listing.title,
                category=b.listing.category,
                category_label=CATEGORY_LABELS[b.listing.category],
                expected_price=b.listing.expected_price,
                status=b.listing.status,
                cover_image=b.listing.images[0].url if b.listing.images else None,
            ),
        )
        for b in bids
    ]
    return CursorPage[MyBidOut](items=items, **result.meta(limit), offset=0)



