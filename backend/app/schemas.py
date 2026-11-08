"""Section 5: Pydantic schemas."""

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from app.db.enums import BidStatus, Category, ListingStatus, Role
from app.db.models import User

# Decimals are serialised as JSON *numbers* (Pydantic v2 would emit strings).
Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]

T = TypeVar("T")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\-\s]{6,18}$")


from app.core.cursor_pagination import CursorPage

class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ---- users / auth ----------------------------------------------------------
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: EmailStr
    role: Role
    display_name: str
    phone: str | None
    city: str | None
    latitude: float | None
    longitude: float | None
    avatar_url: str | None
    rating_avg: float
    rating_count: int
    created_at: datetime


class UserBrief(BaseModel):
    """Name + reputation shown next to listings, bids and reviews."""

    id: uuid.UUID
    display_name: str
    avatar_url: str | None = None
    city: str | None = None
    rating_avg: float = 0.0
    rating_count: int = 0
    phone: str | None = None  # only revealed to the counterpart of an accepted deal


def user_brief(u: User, include_phone: bool = False) -> UserBrief:
    return UserBrief(
        id=u.id,
        display_name=u.display_name,
        avatar_url=u.avatar_url,
        city=u.city,
        rating_avg=round(u.rating_avg or 0.0, 2),
        rating_count=u.rating_count or 0,
        phone=u.phone if include_phone else None,
    )


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=2, max_length=80)
    role: Role
    phone: str | None = None
    city: str | None = Field(default=None, max_length=80)

    @field_validator("display_name", "city")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        return _validate_phone(v)


def _validate_phone(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if not PHONE_RE.match(v):
        raise ValueError("Invalid phone number")
    return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthIn(BaseModel):
    id_token: str
    role: Role | None = None  # required the first time a Google user signs up


class RefreshIn(BaseModel):
    refresh_token: str


class ChangePasswordIn(BaseModel):
    current_password: str | None = None  # None allowed for Google-only accounts setting a first password
    new_password: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    is_new_user: bool = False
    user: UserOut


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=80)
    phone: str | None = None
    city: str | None = Field(default=None, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    avatar_url: str | None = None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        return _validate_phone(v)


class PublicProfile(UserBrief):
    role: Role
    member_since: datetime
    completed_pickups: int


class MonthlyBreakdown(BaseModel):
    month: str
    amount: float = 0.0


class CategoryWeight(BaseModel):
    category: str
    weight_kg: float = 0.0


class RateOut(BaseModel):
    name: str
    price: float
    unit: str = "kg"
    trend: str = "0"


class StatsOut(BaseModel):
    role: Role
    completed_pickups: int
    total_amount: Money
    amount_label: Literal["earned", "paid"]
    active_pickups: int
    open_items: int  # seller: open listings | collector: pending bids
    rating_avg: float
    rating_count: int
    recycled_kg: float = 0.0
    co2_saved_kg: float = 0.0
    wallet_balance: Money = Decimal("0.00")
    monthly_earnings: list[MonthlyBreakdown] = Field(default_factory=list)
    saved_addresses: list[str] = Field(default_factory=list)
    van_inventory: list[CategoryWeight] = Field(default_factory=list)


# ---- listings --------------------------------------------------------------
class ImageOut(BaseModel):
    id: uuid.UUID
    url: str


class BidSummary(BaseModel):
    id: uuid.UUID
    amount: Money
    note: str | None
    status: BidStatus


class ListingCreate(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    category: Category
    weight_kg: Money = Field(gt=0, le=100000)
    expected_price: Money = Field(ge=0, le=10_000_000)
    address: str = Field(min_length=5, max_length=500)
    description: str | None = Field(default=None, max_length=1500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    image_urls: list[str] = Field(default_factory=list, description="URLs returned by POST /uploads/images")
    pickup_time_slot: str | None = Field(default=None, max_length=80)
    floor_access: str | None = Field(default=None, max_length=80)
    is_negotiable: bool = Field(default=True)
    packaging_type: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _coords_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self

    @field_validator("title", "address")
    @classmethod
    def _strip(cls, v):
        return v.strip()


class ListingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=120)
    category: Category | None = None
    weight_kg: Money | None = Field(default=None, gt=0, le=100000)
    expected_price: Money | None = Field(default=None, ge=0, le=10_000_000)
    address: str | None = Field(default=None, min_length=5, max_length=500)
    description: str | None = Field(default=None, max_length=1500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    image_urls: list[str] | None = None
    pickup_time_slot: str | None = Field(default=None, max_length=80)
    floor_access: str | None = Field(default=None, max_length=80)
    is_negotiable: bool | None = None
    packaging_type: str | None = Field(default=None, max_length=80)


class ListingOut(BaseModel):
    id: uuid.UUID
    title: str
    category: Category
    category_label: str
    weight_kg: Money
    expected_price: Money
    address: str
    description: str | None
    latitude: float | None  # exact for the seller / assigned collector, ~1 km approximate for others
    longitude: float | None
    distance_km: float | None
    status: ListingStatus
    current_step: int
    images: list[ImageOut]
    seller: UserBrief
    collector: UserBrief | None
    bid_count: int
    my_bid: BidSummary | None  # the viewing collector's own bid
    final_price: Money | None
    is_owner: bool
    is_assigned_collector: bool
    can_review: bool
    has_reviewed: bool
    pickup_time_slot: str | None = None
    floor_access: str | None = None
    is_negotiable: bool = True
    packaging_type: str | None = None
    created_at: datetime
    accepted_at: datetime | None
    completed_at: datetime | None


class UploadedImage(BaseModel):
    url: str
    key: str


# ---- bids ------------------------------------------------------------------
class BidCreate(BaseModel):
    amount: Money = Field(gt=0, le=10_000_000)
    note: str | None = Field(default=None, max_length=300)

    @field_validator("note")
    @classmethod
    def _strip(cls, v):
        return v.strip() or None if isinstance(v, str) else v


class BidOut(BaseModel):
    id: uuid.UUID
    listing_id: uuid.UUID
    amount: Money
    note: str | None
    status: BidStatus
    is_highest: bool = False
    created_at: datetime
    collector: UserBrief


class ListingMini(BaseModel):
    id: uuid.UUID
    title: str
    category: Category
    category_label: str
    expected_price: Money
    status: ListingStatus
    cover_image: str | None


class MyBidOut(BaseModel):
    id: uuid.UUID
    amount: Money
    note: str | None
    status: BidStatus
    created_at: datetime
    listing: ListingMini


# ---- tracker ---------------------------------------------------------------
class TrackerStep(BaseModel):
    step: int
    status: ListingStatus
    label: str
    done: bool
    current: bool
    timestamp: datetime | None


class TrackerOut(BaseModel):
    listing_id: uuid.UUID
    status: ListingStatus
    current_step: int
    total_steps: int
    cancelled: bool
    steps: list[TrackerStep]
    next_status: ListingStatus | None
    next_label: str | None
    can_advance: bool
    final_price: Money | None
    seller: UserBrief
    collector: UserBrief | None


class AdvanceIn(BaseModel):
    expected_status: ListingStatus | None = Field(
        default=None, description="Optional guard against double taps: fails with 409 if the status already moved."
    )


# ---- reviews ---------------------------------------------------------------
class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)

    @field_validator("comment")
    @classmethod
    def _strip(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v


class ReviewOut(BaseModel):
    id: uuid.UUID
    rating: int
    comment: str | None
    created_at: datetime
    listing_id: uuid.UUID
    listing_title: str
    reviewer: UserBrief
    reviewer_role: Role



# ---- messages (chat) -------------------------------------------------------
class MessageCreate(BaseModel):
    listing_id: uuid.UUID
    recipient_id: uuid.UUID
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("body")
    @classmethod
    def _strip(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty")
        return v


class MessageOut(BaseModel):
    id: uuid.UUID
    listing_id: uuid.UUID
    listing_title: str
    sender_id: uuid.UUID
    sender_name: str
    recipient_id: uuid.UUID
    recipient_name: str
    body: str
    created_at: datetime
