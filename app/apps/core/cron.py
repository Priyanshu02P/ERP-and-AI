"""Shared guard for Vercel Cron endpoints (`Authorization: Bearer $CRON_SECRET`)."""
from hmac import compare_digest

from django.conf import settings


def cron_authorized(request) -> bool:
    if not settings.CRON_SECRET:
        return False
    expected = f"Bearer {settings.CRON_SECRET}"
    return compare_digest(request.headers.get("Authorization", "").encode(), expected.encode())
