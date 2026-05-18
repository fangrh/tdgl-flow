"""Background task that cleans up expired and failed viewer sessions."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from viewer_manager.config import Settings
from viewer_manager.db import session_scope
from viewer_manager.k8s_client import delete_viewer_pod
from viewer_manager.models import ViewerSession

logger = logging.getLogger(__name__)


def cleanup_expired_sessions(session_factory, settings: Settings) -> int:
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.session_idle_ttl_minutes)
    cleaned = 0

    with session_scope(session_factory) as session:
        expired = session.execute(
            select(ViewerSession).where(
                ViewerSession.active_clients == 0,
                ViewerSession.last_accessed_at < cutoff,
                ViewerSession.status.in_(["READY", "STARTING", "PENDING"]),
            )
        ).scalars().all()

        for vs in expired:
            logger.info("Expiring session %s (idle since %s)", vs.session_id, vs.last_accessed_at)
            vs.status = "EXPIRED"
            if vs.pod_name:
                delete_viewer_pod(vs.session_id, settings.k8s_namespace)
            vs.status = "CLEANED"
            cleaned += 1
        session.commit()

    return cleaned


def cleanup_failed_sessions(session_factory, settings: Settings) -> int:
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.failed_cleanup_minutes)
    cleaned = 0

    with session_scope(session_factory) as session:
        failed = session.execute(
            select(ViewerSession).where(
                ViewerSession.status == "FAILED",
                ViewerSession.created_at < cutoff,
            )
        ).scalars().all()

        for vs in failed:
            logger.info("Cleaning failed session %s", vs.session_id)
            if vs.pod_name:
                delete_viewer_pod(vs.session_id, settings.k8s_namespace)
            vs.status = "CLEANED"
            cleaned += 1
        session.commit()

    return cleaned


async def cleanup_loop(session_factory, settings: Settings) -> None:
    while True:
        try:
            expired = cleanup_expired_sessions(session_factory, settings)
            failed = cleanup_failed_sessions(session_factory, settings)
            if expired or failed:
                logger.info("Cleanup: expired=%d, failed=%d", expired, failed)
        except Exception:
            logger.exception("Cleanup task error")
        await asyncio.sleep(settings.cleanup_interval_seconds)