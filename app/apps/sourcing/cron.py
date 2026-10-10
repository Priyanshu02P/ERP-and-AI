"""Vercel Cron endpoint. Vercel sends `Authorization: Bearer $CRON_SECRET`."""
from hmac import compare_digest

from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse

from . import services


def close_rfqs(request):
    token = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.CRON_SECRET}"
    if not settings.CRON_SECRET or not compare_digest(token.encode(), expected.encode()):
        return HttpResponseForbidden("Forbidden")
    return JsonResponse({"closed": services.close_expired_rfqs()})
