"""Vercel Cron endpoint: close RFQs whose response deadline has passed."""
from django.http import HttpResponseForbidden, JsonResponse

from apps.core.cron import cron_authorized

from . import services


def close_rfqs(request):
    if not cron_authorized(request):
        return HttpResponseForbidden("Forbidden")
    return JsonResponse({"closed": services.close_expired_rfqs()})
