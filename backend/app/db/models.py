"""Section 4: ORM models."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow
from app.db.enums import BidStatus, Category, ListingStatus, Role, pg_enum


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)
    role: Mapped[Role] = mapped_column(pg_enum(Role), index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str | None] = mapped_column(String(20))
    city: Mapped[str | None] = mapped_column(String(80))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    # cached reputation (recomputed whenever a review is added)
    rating_avg: Mapped[float] = mapped_column(Float, default=0.0)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        CheckConstraint("weight_kg > 0", name="ck_listing_weight_positive"),
        CheckConstraint("expected_price >= 0", name="ck_listing_price_nonneg"),
        Index("ix_listings_feed", "status", "category", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    title: Mapped[str] = mapped_column(String(120))
    category: Mapped[Category] = mapped_column(pg_enum(Category), index=True)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    expected_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    address: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    pickup_time_slot: Mapped[str | None] = mapped_column(String(80), nullable=True)
    floor_access: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_negotiable: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    packaging_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[ListingStatus] = mapped_column(pg_enum(ListingStatus), default=ListingStatus.POSTED, index=True)
    collector_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    final_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    images: Mapped[list["ListingImage"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan", order_by="ListingImage.position", lazy="selectin"
    )
    events: Mapped[list["PickupEvent"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan", order_by="PickupEvent.created_at", lazy="select"
    )


class ListingImage(Base):
    __tablename__ = "listing_images"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(600))
    storage_key: Mapped[str] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(Integer, default=0)

    listing: Mapped[Listing] = relationship(back_populates="images")


class PickupEvent(Base):
    """Audit trail of every status change - powers the tracker timestamps."""

    __tablename__ = "pickup_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    status: Mapped[ListingStatus] = mapped_column(pg_enum(ListingStatus))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listing: Mapped[Listing] = relationship(back_populates="events")


class Bid(Base):
    __tablename__ = "bids"
    __table_args__ = (
        UniqueConstraint("listing_id", "collector_id", name="uq_bid_listing_collector"),
        CheckConstraint("amount > 0", name="ck_bid_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    collector_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    note: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[BidStatus] = mapped_column(pg_enum(BidStatus), default=BidStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    listing: Mapped[Listing] = relationship(foreign_keys=[listing_id], lazy="selectin")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("listing_id", "reviewer_id", name="uq_review_listing_reviewer"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_review_rating_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    reviewee_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    rating: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    listing: Mapped[Listing] = relationship(foreign_keys=[listing_id], lazy="selectin")


class Message(Base):
    """Buyer/seller chat about a listing (added so the frontend's Chats tab keeps working)."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_listing_created", "listing_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    sender_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    recipient_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    listing: Mapped[Listing] = relationship(foreign_keys=[listing_id], lazy="selectin")


class ChatReadState(Base):
    """Watermark of what reader_id has read from peer_id in a listing's conversation."""

    __tablename__ = "chat_read_states"

    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    reader_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    peer_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    last_read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

