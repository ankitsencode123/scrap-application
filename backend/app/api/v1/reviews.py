"""Reviews."""

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

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
from app.core.security import CurrentUser
from app.core.shard_router import get_router, merge_page
from app.db.base import DB
from app.db.enums import ListingStatus
from app.db.models import Listing, Review, User
from app.schemas import Page, ReviewCreate, ReviewOut
from app.services.common import review_out

reviews_router = APIRouter(tags=["Reviews"])


# ---------------------------------------------------------------- REVIEWS ---
@reviews_router.post("/listings/{listing_id}/reviews", response_model=ReviewOut, status_code=201)
def create_review(listing_id: uuid.UUID, payload: ReviewCreate, db: DB, user: CurrentUser):
    s_db = db.for_id(listing_id)
    listing = s_db.get(Listing, listing_id)
    if not listing or user.id not in (listing.seller_id, listing.collector_id):
        raise HTTPException(404, "Listing not found")
    if listing.status != ListingStatus.COMPLETED:
        raise HTTPException(409, "You can leave a review once the pickup is completed")
    reviewee_id = listing.collector_id if user.id == listing.seller_id else listing.seller_id
    if reviewee_id is None:
        raise HTTPException(409, "No counterpart to review")

    router = get_router()
    review = Review(
        id=router.colocated_id(listing.id),
        listing_id=listing.id,
        reviewer_id=user.id,
        reviewee_id=reviewee_id,
        rating=payload.rating,
        comment=payload.comment,
    )
    s_db.add(review)
    try:
        s_db.commit()
    except IntegrityError:
        s_db.rollback()
        raise HTTPException(409, "You have already reviewed this pickup")

    # Update reviewee cached rating on reviewee's shard
    reviewee_db = db.for_id(reviewee_id)
    reviewee = reviewee_db.scalar(select(User).where(User.id == reviewee_id).with_for_update())

    # Aggregate rating across all shards (since reviews are colocated with their listings)
    res = router.scatter(
        lambda session, sid: session.execute(
            select(func.coalesce(func.sum(Review.rating), 0), func.count(Review.id)).where(Review.reviewee_id == reviewee_id)
        ).one()
    )
    total_sum = sum(s for s, _ in res.values())
    total_cnt = sum(c for _, c in res.values())
    if reviewee:
        reviewee.rating_avg = round(float(total_sum / total_cnt), 2) if total_cnt > 0 else 0.0
        reviewee.rating_count = int(total_cnt)
        reviewee_db.commit()

    s_db.refresh(review)
    return review_out(review, reviewer=user)


def _reviews_page(
    db: DB,
    user_id: uuid.UUID,
    limit: LimitQuery = 20,
    cursor: CursorQuery = None,
    include_total: bool = True,
    offset: int = 0,
) -> CursorPage[ReviewOut]:
    filters = [Review.reviewee_id == user_id]
    router = get_router()
    spec = SortSpec(
        "newest",
        [
            SortColumn("created_at", Review.created_at, DATETIME, lambda r: r[0].created_at, descending=True),
            SortColumn("id", Review.id, UUID_, lambda r: r[0].id, descending=True),
        ],
    )

    if offset > 0 and cursor is None:
        fetch_limit = offset + limit
        res = router.scatter(
            lambda s_db, sid: (
                s_db.scalar(select(func.count(Review.id)).where(*filters)) or 0,
                list(s_db.scalars(select(Review).where(*filters).order_by(Review.created_at.desc(), Review.id.desc()).limit(fetch_limit))),
            )
        )
        total = sum(c for c, _ in res.values())
        per_shard_reviews = [r for _, r in res.values()]
        reviews = merge_page(per_shard_reviews, key=lambda r: r.created_at.timestamp(), limit=limit, offset=offset, reverse=True)
        reviewer_ids = {r.reviewer_id for r in reviews}
        users = router.load_by_ids(User, reviewer_ids)
        items = [review_out(r, reviewer=users.get(r.reviewer_id)) for r in reviews]
        return CursorPage[ReviewOut](items=items, total=total, limit=limit, offset=offset, has_more=len(reviews) == limit)

    fp = make_fingerprint(reviewee=user_id)
    result = paginate_sharded(
        router,
        base=select(Review),
        spec=spec,
        filters=filters,
        limit=limit,
        cursor=cursor,
        fingerprint=fp,
        with_total=include_total,
        count_stmt=select(func.count(Review.id)),
    )
    reviews = [r[0] for r in result.rows]
    reviewer_ids = {r.reviewer_id for r in reviews}
    users = router.load_by_ids(User, reviewer_ids)
    items = [review_out(r, reviewer=users.get(r.reviewer_id)) for r in reviews]
    return CursorPage[ReviewOut](items=items, **result.meta(limit), offset=0)



