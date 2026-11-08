"""Chat messages between a seller and collectors about a listing."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import or_, select

from app.core.security import CurrentUser
from app.core.shard_router import get_router, merge_page
from app.db.base import DB
from app.db.enums import ListingStatus
from app.db.models import Listing, Message, User
from app.schemas import MessageCreate, MessageOut

messages_router = APIRouter(prefix="/messages", tags=["Messages"])


def message_out(m: Message, sender: User | None = None, recipient: User | None = None) -> MessageOut:
    return MessageOut(
        id=m.id,
        listing_id=m.listing_id,
        listing_title=m.listing.title if m.listing else "",
        sender_id=m.sender_id,
        sender_name=sender.display_name if sender else "",
        recipient_id=m.recipient_id,
        recipient_name=recipient.display_name if recipient else "",
        body=m.body,
        created_at=m.created_at,
    )


@messages_router.get("", response_model=list[MessageOut])
def my_messages(
    db: DB,
    user: CurrentUser,
    listing_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
):
    """Every message you sent or received (oldest first), optionally for one listing."""
    filters = [or_(Message.sender_id == user.id, Message.recipient_id == user.id)]
    if listing_id:
        filters.append(Message.listing_id == listing_id)
        s_db = db.for_id(listing_id)
        rows = list(s_db.scalars(select(Message).where(*filters).order_by(Message.created_at.desc()).limit(limit)))
    else:
        router = get_router()
        res = router.scatter(
            lambda s_db, sid: list(s_db.scalars(select(Message).where(*filters).order_by(Message.created_at.desc()).limit(limit)))
        )
        rows = merge_page(res.values(), key=lambda m: m.created_at.timestamp(), limit=limit, offset=0, reverse=True)

    user_ids = {m.sender_id for m in rows} | {m.recipient_id for m in rows}
    users = get_router().load_by_ids(User, user_ids)
    if user.id in user_ids:
        users[user.id] = user

    out = [
        message_out(m, sender=users.get(m.sender_id), recipient=users.get(m.recipient_id))
        for m in reversed(rows)
    ]

    # Merge messages from the dedicated Supabase Realtime chat database
    try:
        from app.db.chat_db import fetch_supabase_messages

        sb_msgs = fetch_supabase_messages(str(user.id), str(listing_id) if listing_id else None, limit=limit)
        existing_ids = {str(m.id) for m in out}
        for sm in reversed(sb_msgs):
            m_id = sm.get("message_id") or str(sm["id"])
            if m_id not in existing_ids:
                try:
                    out.append(
                        MessageOut(
                            id=uuid.UUID(m_id) if len(m_id) == 36 else uuid.uuid4(),
                            listing_id=uuid.UUID(sm["listing_id"]) if len(sm.get("listing_id", "")) == 36 else uuid.uuid4(),
                            listing_title=sm.get("listing_title") or "",
                            sender_id=uuid.UUID(sm["sender_id"]) if len(sm.get("sender_id", "")) == 36 else user.id,
                            sender_name=sm.get("sender_name") or "",
                            recipient_id=uuid.UUID(sm["recipient_id"]) if len(sm.get("recipient_id", "")) == 36 else user.id,
                            recipient_name=sm.get("recipient_name") or "",
                            body=sm.get("body") or "",
                            created_at=sm.get("created_at"),
                        )
                    )
                    existing_ids.add(m_id)
                except Exception:
                    pass
    except Exception:
        pass

    return sorted(out, key=lambda x: x.created_at)


@messages_router.post("", response_model=MessageOut, status_code=201)
def send_message(payload: MessageCreate, db: DB, user: CurrentUser):
    """
    One side must be the listing's seller. While the listing is still open, any collector may talk to
    the seller (questions before bidding); afterwards only the seller and the assigned collector can.
    """
    s_db = db.for_id(payload.listing_id)
    listing = s_db.get(Listing, payload.listing_id)
    if not listing:
        raise HTTPException(404, "Listing not found")
    recipient = db.get(User, payload.recipient_id)
    if not recipient or not recipient.is_active or recipient.id == user.id:
        raise HTTPException(404, "Recipient not found")
    pair = {user.id, recipient.id}
    if listing.seller_id not in pair:
        raise HTTPException(403, "Chats are only between the seller and a collector of this listing")
    other = recipient if user.id == listing.seller_id else user
    if other.role.value != "collector":
        raise HTTPException(403, "Chats are only between the seller and a collector of this listing")
    if listing.status != ListingStatus.POSTED and other.id != listing.collector_id:
        raise HTTPException(403, "This listing is already assigned to another collector")

    router = get_router()
    m = Message(
        id=router.colocated_id(listing.id),
        listing_id=listing.id,
        sender_id=user.id,
        recipient_id=recipient.id,
        body=payload.body,
    )
    s_db.add(m)
    s_db.commit()
    s_db.refresh(m)
    out = message_out(m, sender=user, recipient=recipient)

    # Insert into Supabase Realtime database for instant broadcast
    try:
        from app.db.chat_db import insert_supabase_message

        insert_supabase_message(
            message_id=str(m.id),
            listing_id=str(listing.id),
            listing_title=listing.title,
            sender_id=str(user.id),
            sender_name=user.display_name,
            recipient_id=str(recipient.id),
            recipient_name=recipient.display_name,
            body=payload.body,
        )
    except Exception:
        pass

    from app.api.v1.chat_ws import announce_http_message

    announce_http_message(out)
    return out


