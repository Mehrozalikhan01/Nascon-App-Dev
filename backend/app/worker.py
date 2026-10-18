"""Small idempotent consumer for demo order-paid events."""

import json
import logging
import os
import time
from pathlib import Path

from redis import Redis

from app.services.object_store import archive_receipt_pdf, archive_vod_recording


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("nascon.worker")


def run() -> None:
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    minio_endpoint = os.environ.get("MINIO_ENDPOINT", "")
    minio_access_key = os.environ.get("MINIO_ACCESS_KEY", "")
    minio_secret_key = os.environ.get("MINIO_SECRET_KEY", "")
    minio_bucket = os.environ.get("MINIO_BUCKET", "nascon-receipts")
    minio_secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
    client = Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=10,
        health_check_interval=30,
    )
    recording_root = Path(os.environ.get("RECORDINGS_DIR", "/recordings"))
    stable_sizes: dict[str, tuple[int, float]] = {}

    def archive_completed_recordings() -> None:
        if not recording_root.is_dir() or not minio_endpoint:
            return
        for path in recording_root.rglob("*.mp4"):
            try:
                size = path.stat().st_size
                previous = stable_sizes.get(str(path))
                if previous is None or previous[0] != size:
                    stable_sizes[str(path)] = (size, time.monotonic())
                    continue
                if time.monotonic() - previous[1] < 8:
                    continue
                relative = path.relative_to(recording_root).as_posix()
                if client.sismember("nascon:archived_vod", relative):
                    continue
                object_name = archive_vod_recording(
                    str(path), str(recording_root), minio_endpoint, minio_access_key,
                    minio_secret_key, minio_bucket, minio_secure,
                )
                client.sadd("nascon:archived_vod", relative)
                client.rpush("nascon:vod:index", json.dumps({"recording": relative, "object": object_name}))
                logger.info("Archived completed VOD %s", object_name)
            except FileNotFoundError:
                stable_sizes.pop(str(path), None)
            except Exception:
                logger.exception("Could not archive recording %s", path)

    while True:
        try:
            archive_completed_recordings()
            # Recover a job left in-flight if the worker exited before acking.
            client.rpoplpush("nascon:events:processing", "nascon:events")
            item = client.brpoplpush(
                "nascon:events", "nascon:events:processing", timeout=5
            )
            if item is None:
                continue

            event = json.loads(item)
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                logger.warning("Discarding malformed event without an id")
                client.lrem("nascon:events:processing", 1, item)
                continue

            if event.get("type") == "order.paid":
                object_name = archive_receipt_pdf(
                    str(event.get("order_id", "")),
                    str(event.get("receipt_pdf_base64", "")),
                    str(event.get("receipt_signature", "")),
                    minio_endpoint,
                    minio_access_key,
                    minio_secret_key,
                    minio_bucket,
                    minio_secure,
                )
                logger.info("Archived receipt %s", object_name)

            processed = client.hsetnx(
                "nascon:processed_events",
                event_id,
                json.dumps({"type": event.get("type"), "order_id": event.get("order_id")}),
            )
            if processed:
                logger.info("Processed event %s", event_id)
            client.lrem("nascon:events:processing", 1, item)
        except Exception:
            logger.exception("Order event worker could not reach Redis or parse a job")
            time.sleep(2)


if __name__ == "__main__":
    run()
