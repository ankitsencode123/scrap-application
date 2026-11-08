"""Direct Supabase Realtime Chat Database Integration.

Stores and queries chat messages from the dedicated real-time Supabase PostgreSQL instance:
postgresql://postgres.khdbvhcdvtqmreatcssb:74397643438420717330@aws-0-us-east-1.pooler.supabase.com:5432/postgres
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from app.core.config import settings

log = logging.getLogger("scrapmart.chat_db")


def _get_raw_url() -> str:
    url = getattr(settings, "CHAT_DATABASE_URL", "")
    if not url:
        url = "postgresql://postgres.khdbvhcdvtqmreatcssb:74397643438420717330@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql://", 1)
    return url


def insert_supabase_message(
    message_id: str,
    listing_id: str,
    listing_title: str,
    sender_id: str,
    sender_name: str,
    recipient_id: str,
    recipient_name: str,
    body: str,
    client_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Inserts a message row into public.messages in the Supabase Realtime project."""
    try:
        with psycopg2.connect(_get_raw_url(), connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO public.messages (
                        message_id, listing_id, listing_title, sender_id, sender_name,
                        recipient_id, recipient_name, body, client_id, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, message_id, listing_id, listing_title, sender_id, sender_name,
                              recipient_id, recipient_name, body, client_id, created_at;
                    """,
                    (
                        str(message_id),
                        str(listing_id),
                        listing_title or "Scrap Listing",
                        str(sender_id),
                        sender_name or "User",
                        str(recipient_id),
                        recipient_name or "User",
                        body,
                        client_id,
                        datetime.now(timezone.utc),
                    ),
                )
                row = cur.fetchone()
                # Broadcast via PostgreSQL NOTIFY so any live listeners receive it instantly
                with conn.cursor() as notify_cur:
                    notify_cur.execute("NOTIFY chat_messages, %s;", (str(message_id),))
                return dict(row) if row else None
    except Exception as exc:
        log.warning("Supabase chat DB insert failed: %s", exc)
        return None


def fetch_supabase_messages(
    user_id: str, listing_id: Optional[str] = None, limit: int = 1000
) -> list[dict[str, Any]]:
    """Queries messages involving user_id from the Supabase Realtime database."""
    try:
        with psycopg2.connect(_get_raw_url(), connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if listing_id:
                    cur.execute(
                        """
                        SELECT id, message_id, listing_id, listing_title, sender_id, sender_name,
                               recipient_id, recipient_name, body, client_id, created_at
                        FROM public.messages
                        WHERE (sender_id = %s OR recipient_id = %s) AND listing_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s;
                        """,
                        (str(user_id), str(user_id), str(listing_id), limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, message_id, listing_id, listing_title, sender_id, sender_name,
                               recipient_id, recipient_name, body, client_id, created_at
                        FROM public.messages
                        WHERE sender_id = %s OR recipient_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s;
                        """,
                        (str(user_id), str(user_id), limit),
                    )
                return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        log.warning("Supabase chat DB fetch failed: %s", exc)
        return []
