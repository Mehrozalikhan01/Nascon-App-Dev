"""MinIO-backed archive for generated order receipts."""

import base64
from io import BytesIO
from pathlib import Path
import re
import time

def make_minio_client(endpoint: str, access_key: str, secret_key: str, secure: bool = False):
    from minio import Minio

    if not endpoint or not access_key or not secret_key:
        raise ValueError("Object-store endpoint and credentials are required")
    return Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)


def ensure_receipt_bucket(endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool = False) -> None:
    client = make_minio_client(endpoint, access_key, secret_key, secure)
    last_error = None
    for attempt in range(12):
        try:
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
            return
        except Exception as error:
            last_error = error
            time.sleep(min(0.25 * (attempt + 1), 2.0))
    raise RuntimeError("MinIO did not become ready for receipt storage") from last_error


def archive_receipt_pdf(
    order_id: str,
    encoded_pdf: str,
    signature: str,
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    secure: bool = False,
) -> str:
    """Store a verified PDF under a stable, idempotent object name."""
    pdf = base64.b64decode(encoded_pdf, validate=True)
    if len(pdf) < 8 or not pdf.startswith(b"%PDF-"):
        raise ValueError("Receipt payload is not a PDF")
    if not order_id or len(order_id) > 64:
        raise ValueError("Invalid receipt order id")

    client = make_minio_client(endpoint, access_key, secret_key, secure)
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    object_name = f"receipts/{order_id}.pdf"
    client.put_object(
        bucket,
        object_name,
        BytesIO(pdf),
        length=len(pdf),
        content_type="application/pdf",
        metadata={"receipt-signature": signature},
    )
    return object_name


def archive_vod_recording(
    recording_path: str,
    recording_root: str,
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    secure: bool = False,
) -> str:
    """Upload a completed MediaMTX recording beneath the VOD prefix."""
    root = Path(recording_root).resolve()
    source = Path(recording_path).resolve()
    try:
        relative = source.relative_to(root)
    except ValueError as error:
        raise ValueError("Recording path must remain inside its shared volume") from error
    if not source.is_file() or source.stat().st_size < 12:
        raise ValueError("Recording is missing or too small")
    safe_parts = []
    for part in relative.parts:
        clean = re.sub(r"[^A-Za-z0-9_.-]", "_", part)
        if clean in {"", ".", ".."}:
            raise ValueError("Invalid recording path")
        safe_parts.append(clean)
    object_name = "vod/" + "/".join(safe_parts)
    client = make_minio_client(endpoint, access_key, secret_key, secure)
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.fput_object(bucket, object_name, str(source), content_type="video/mp4")
    return object_name
