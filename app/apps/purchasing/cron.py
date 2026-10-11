"""Vercel Cron endpoint: generate release POs for recurring agreements that are due."""
from django.http import HttpResponseForbidden, JsonResponse

from apps.core.cron import cron_authorized

from . import services


def generate_releases(request):
    if not cron_authorized(request):
        return HttpResponseForbidden("Forbidden")
    return JsonResponse(services.generate_due_releases())
