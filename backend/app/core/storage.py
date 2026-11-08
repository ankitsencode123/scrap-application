"""Section 7: image storage (S3-compatible or local disk) + image processing."""

import io
from pathlib import Path

from fastapi import HTTPException, status
from PIL import Image, ImageOps

from app.core.config import log, settings


class StorageBackend:
    def save(self, data: bytes, key: str, content_type: str = "image/jpeg") -> str:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def get(self, key: str) -> tuple[bytes, str] | None:
        raise NotImplementedError

    def public_prefix(self) -> str:
        raise NotImplementedError

    def key_from_url(self, url: str) -> str | None:
        prefix = self.public_prefix() + "/"
        return url[len(prefix):] if url.startswith(prefix) and len(url) > len(prefix) else None


class LocalStorage(StorageBackend):
    def __init__(self, base_dir: str, public_base: str):
        self.base = Path(base_dir).resolve()
        self.base.mkdir(parents=True, exist_ok=True)
        self.public_base = public_base

    def public_prefix(self) -> str:
        return f"{self.public_base}/media"

    def save(self, data: bytes, key: str, content_type: str = "image/jpeg") -> str:
        path = (self.base / key).resolve()
        if self.base not in path.parents:
            raise HTTPException(400, "Invalid storage key")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"{self.public_prefix()}/{key}"

    def get(self, key: str) -> tuple[bytes, str] | None:
        path = (self.base / key).resolve()
        if self.base in path.parents and path.exists():
            return path.read_bytes(), "image/jpeg"
        return None

    def delete(self, key: str) -> None:
        path = (self.base / key).resolve()
        if self.base in path.parents:
            path.unlink(missing_ok=True)


class S3Storage(StorageBackend):
    """Works with AWS S3, Cloudflare R2, Supabase Storage, MinIO, DigitalOcean Spaces..."""

    def __init__(self):
        import boto3  # lazy import

        self.bucket = settings.S3_BUCKET
        self.client = boto3.client(
            "s3",
            region_name=settings.S3_REGION,
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        )
        self.base_url = settings.S3_PUBLIC_BASE_URL or f"https://{self.bucket}.s3.{settings.S3_REGION}.amazonaws.com"

    def public_prefix(self) -> str:
        return self.base_url

    def save(self, data: bytes, key: str, content_type: str = "image/jpeg") -> str:
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable",
            )
        except Exception:
            log.exception("S3 upload failed")
            raise HTTPException(502, "Image storage is unavailable, try again")
        return f"{self.base_url}/{key}"

    def get(self, key: str) -> tuple[bytes, str] | None:
        try:
            res = self.client.get_object(Bucket=self.bucket, Key=key)
            return res["Body"].read(), res.get("ContentType", "image/jpeg")
        except Exception:
            return None

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception:
            log.warning("Could not delete %s from S3", key)


class SupabaseStorage(StorageBackend):
    """Persistent storage backed by Supabase Storage Bucket & Supabase PostgreSQL (media_files table)."""

    def __init__(
        self,
        db_url: str,
        public_base: str,
        supabase_url: str = "",
        supabase_key: str = "",
        bucket: str = "scrapmart-media",
    ):
        self.raw_url = (
            db_url.replace("postgresql+psycopg2://", "postgresql://", 1)
            if db_url.startswith("postgresql+psycopg2://")
            else db_url
        )
        self.public_base = public_base.rstrip("/")
        self.supabase_url = (supabase_url or "https://khdbvhcdvtqmreatcssb.supabase.co").rstrip("/")
        self.supabase_key = supabase_key
        self.bucket = bucket

    def public_prefix(self) -> str:
        if self.supabase_key:
            return f"{self.supabase_url}/storage/v1/object/public/{self.bucket}"
        return f"{self.public_base}/media"

    def save(self, data: bytes, key: str, content_type: str = "image/jpeg") -> str:
        # If Supabase API key is provided, upload directly to Supabase Storage bucket
        if self.supabase_key:
            import httpx

            try:
                upload_url = f"{self.supabase_url}/storage/v1/object/{self.bucket}/{key}"
                headers = {
                    "Authorization": f"Bearer {self.supabase_key}",
                    "apikey": self.supabase_key,
                    "Content-Type": content_type,
                    "x-upsert": "true",
                }
                resp = httpx.post(upload_url, headers=headers, content=data, timeout=10.0)
                if resp.status_code in (200, 201):
                    return f"{self.public_prefix()}/{key}"
                log.warning("Supabase Storage REST upload returned %d: %s, falling back to DB", resp.status_code, resp.text)
            except Exception:
                log.exception("Supabase Storage REST upload error, falling back to DB")

        # Direct persistent fallback using PostgreSQL credentials (zero config needed)
        import psycopg2

        try:
            with psycopg2.connect(self.raw_url, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO public.media_files (key, data, content_type, size_bytes)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (key) DO UPDATE SET
                            data = EXCLUDED.data,
                            content_type = EXCLUDED.content_type,
                            size_bytes = EXCLUDED.size_bytes;
                        """,
                        (key, psycopg2.Binary(data), content_type, len(data)),
                    )
            return f"{self.public_base}/media/{key}"
        except Exception:
            log.exception("Supabase media save failed for key %s", key)
            raise HTTPException(502, "Image storage is temporarily unavailable")

    def get(self, key: str) -> tuple[bytes, str] | None:
        if self.supabase_key:
            import httpx

            try:
                public_url = f"{self.supabase_url}/storage/v1/object/public/{self.bucket}/{key}"
                resp = httpx.get(public_url, timeout=5.0)
                if resp.status_code == 200:
                    return resp.content, resp.headers.get("Content-Type", "image/jpeg")
            except Exception:
                pass

        import psycopg2

        try:
            with psycopg2.connect(self.raw_url, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT data, content_type FROM public.media_files WHERE key = %s;", (key,))
                    row = cur.fetchone()
                    if row:
                        return bytes(row[0]), str(row[1])
            return None
        except Exception:
            log.warning("Supabase media fetch failed for key %s", key)
            return None

    def delete(self, key: str) -> None:
        if self.supabase_key:
            import httpx

            try:
                del_url = f"{self.supabase_url}/storage/v1/object/{self.bucket}/{key}"
                headers = {
                    "Authorization": f"Bearer {self.supabase_key}",
                    "apikey": self.supabase_key,
                }
                httpx.delete(del_url, headers=headers, timeout=5.0)
            except Exception:
                pass

        import psycopg2

        try:
            with psycopg2.connect(self.raw_url, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM public.media_files WHERE key = %s;", (key,))
        except Exception:
            log.warning("Could not delete %s from Supabase media_files", key)


def build_storage() -> StorageBackend:
    if settings.S3_BUCKET:
        return S3Storage()
    supabase_db = getattr(settings, "CHAT_DATABASE_URL", "") or (
        settings.DATABASE_URL if "supabase" in settings.DATABASE_URL else ""
    )
    if supabase_db and "supabase" in supabase_db:
        log.info("Using Supabase persistent media storage")
        return SupabaseStorage(
            db_url=supabase_db,
            public_base=settings.PUBLIC_BASE_URL,
            supabase_url=settings.SUPABASE_URL,
            supabase_key=settings.SUPABASE_KEY,
            bucket=settings.SUPABASE_STORAGE_BUCKET,
        )
    log.warning("S3_BUCKET not set - using LOCAL disk storage (development only)")
    return LocalStorage(settings.LOCAL_UPLOAD_DIR, settings.PUBLIC_BASE_URL)


storage = build_storage()

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def process_image(raw: bytes, max_edge: int = 1600) -> bytes:
    """Verify it's a real image, fix rotation, strip EXIF/GPS, downscale, re-encode as JPEG."""
    try:
        probe = Image.open(io.BytesIO(raw))
        if probe.format not in ALLOWED_IMAGE_FORMATS:
            raise ValueError("unsupported format")
        probe.verify()
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((max_edge, max_edge))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85, optimize=True)  # re-encoding drops EXIF (incl. GPS)
        return out.getvalue()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid image. Upload a JPEG, PNG or WEBP file.")

