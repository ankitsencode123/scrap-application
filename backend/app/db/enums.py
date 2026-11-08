"""Section 3: enums & constants."""

import enum

from sqlalchemy import Enum as SAEnum


class Role(str, enum.Enum):
    SELLER = "seller"
    COLLECTOR = "collector"


class Category(str, enum.Enum):
    PAPER_CARDBOARD = "paper_cardboard"
    METALS = "metals"
    PLASTIC_BOTTLES = "plastic_bottles"
    EWASTE_APPLIANCES = "ewaste_appliances"
    GLASS = "glass"
    CLOTHES = "clothes"
    MIXED = "mixed"


CATEGORY_LABELS = {
    Category.PAPER_CARDBOARD: "Paper & Cardboard",
    Category.METALS: "Metals",
    Category.PLASTIC_BOTTLES: "Plastic & Bottles",
    Category.EWASTE_APPLIANCES: "E-Waste & Appliances",
    Category.GLASS: "Glass",
    Category.CLOTHES: "Clothes",
    Category.MIXED: "Mixed scrap",
}


class ListingStatus(str, enum.Enum):
    POSTED = "posted"                    # step 1
    OFFER_ACCEPTED = "offer_accepted"    # step 2
    ON_THE_WAY = "on_the_way"            # step 3
    ARRIVED = "arrived"                  # step 4
    COMPLETED = "completed"              # step 5 (picked up & paid)
    CANCELLED = "cancelled"


PICKUP_FLOW = [
    ListingStatus.POSTED,
    ListingStatus.OFFER_ACCEPTED,
    ListingStatus.ON_THE_WAY,
    ListingStatus.ARRIVED,
    ListingStatus.COMPLETED,
]
STEP_LABELS = {
    ListingStatus.POSTED: "Posted",
    ListingStatus.OFFER_ACCEPTED: "Offer accepted",
    ListingStatus.ON_THE_WAY: "Collector on the way",
    ListingStatus.ARRIVED: "Arrived at doorstep",
    ListingStatus.COMPLETED: "Picked up & paid",
    ListingStatus.CANCELLED: "Cancelled",
}
ACTIVE_PICKUP_STATUSES = [ListingStatus.OFFER_ACCEPTED, ListingStatus.ON_THE_WAY, ListingStatus.ARRIVED]


class BidStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


def pg_enum(enum_cls):
    """Store enums as VARCHAR (no native PG enum -> painless migrations)."""
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=32,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )

