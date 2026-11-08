from fastapi import APIRouter
from sqlalchemy import func, select

from app.core.shard_router import get_router
from app.db.base import DB
from app.db.enums import CATEGORY_LABELS, PICKUP_FLOW, STEP_LABELS, Category
from app.db.models import Listing
from app.schemas import RateOut

meta_router = APIRouter(tags=["Meta"])


# ------------------------------------------------------------------- META ---
@meta_router.get("/meta/categories")
def get_categories():
    return [{"value": c.value, "label": CATEGORY_LABELS[c]} for c in Category]


@meta_router.get("/meta/pickup-steps")
def get_pickup_steps():
    return [{"step": i, "status": s.value, "label": STEP_LABELS[s]} for i, s in enumerate(PICKUP_FLOW, 1)]


@meta_router.get("/meta/rates", response_model=list[RateOut])
def get_rates(db: DB):
    """Scrap rates per kg calculated from active market listings in DB (cached in Redis)."""
    try:
        from app.core.redis_client import cache_get, cache_set
        cached = cache_get("scrapmart:rates")
        if cached and isinstance(cached, list):
            return [RateOut(**item) for item in cached]
    except Exception:
        cache_set = None

    router = get_router()

    def fetch_shard_rates(s_db, sid: int):
        rows = s_db.execute(
            select(
                Listing.category,
                func.count(Listing.id),
                func.avg(Listing.expected_price / func.nullif(Listing.weight_kg, 0)),
            ).where(Listing.weight_kg > 0).group_by(Listing.category)
        ).all()
        return [(cat.value if hasattr(cat, "value") else str(cat), cnt, float(avg_p or 0)) for cat, cnt, avg_p in rows]

    res = router.scatter(fetch_shard_rates)
    cat_counts: dict[str, int] = {}
    cat_weighted_sum: dict[str, float] = {}
    for shard_res in res.values():
        for cat, cnt, avg_p in shard_res:
            cat_counts[cat] = cat_counts.get(cat, 0) + cnt
            cat_weighted_sum[cat] = cat_weighted_sum.get(cat, 0.0) + (avg_p * cnt)

    BASELINES = {
        "paper_cardboard": (14.0, "Newspaper & Cardboard", "+1"),
        "metals": (35.0, "Metals & Iron", "+2"),
        "plastic_bottles": (12.0, "Plastic (PET)", "-1"),
        "ewaste_appliances": (45.0, "E-waste & Electronics", "+3"),
        "glass": (5.0, "Glass Bottles", "0"),
        "clothes": (10.0, "Clothes & Fabrics", "0"),
        "mixed": (18.0, "Mixed Scrap", "+1"),
    }

    rates = []
    for cat_key, (base_price, label, trend) in BASELINES.items():
        cnt = cat_counts.get(cat_key, 0)
        if cnt > 0 and cat_weighted_sum.get(cat_key, 0) > 0:
            price = round(cat_weighted_sum[cat_key] / cnt, 1)
        else:
            price = base_price
        rates.append(RateOut(name=label, price=price, unit="kg", trend=trend))

    if cache_set:
        try:
            cache_set("scrapmart:rates", [r.model_dump() for r in rates], ttl=60)
        except Exception:
            pass

    return rates

