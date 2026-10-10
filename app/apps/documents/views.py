"""Authenticated upload / download / delete. Files are never publicly addressable."""
import requests
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseBadRequest, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.audit.services import record
from apps.core.exceptions import WorkflowError

from . import registry, services
from .models import Document
from .storage import get_backend


def _back(request, fallback="/"):
    target = request.POST.get("next") or fallback
    ok = url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return redirect(target if ok else fallback)


def _owner_or_404(owner_type, owner_id):
    try:
        spec = registry.get(owner_type)
    except KeyError:
        raise Http404
    owner = spec.load(owner_id)
    if owner is None:
        raise Http404
    return spec, owner


@login_required
@require_POST
def upload(request):
    owner_type = request.POST.get("owner_type", "")
    try:
        owner_id = int(request.POST.get("owner_id", ""))
    except ValueError:
        return HttpResponseBadRequest("owner_id required")
    spec, owner = _owner_or_404(owner_type, owner_id)
    if not spec.can_attach(request.user, owner):
        raise PermissionDenied
    file = request.FILES.get("file")
    if not file:
        messages.error(request, "Choose a file to upload.")
        return _back(request)
    try:
        doc = services.attach_file(file, owner_type, owner, request.user)
        messages.success(request, f"Uploaded {doc.original_filename}.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    except requests.RequestException:
        messages.error(request, "File storage is unavailable. Try again shortly.")
    return _back(request)


@login_required
def download(request, pk):
    doc = get_object_or_404(Document, pk=pk, is_deleted=False)
    spec, owner = _owner_or_404(doc.owner_type, doc.owner_id)
    if not spec.can_view(request.user, owner):
        raise PermissionDenied
    try:
        stream = get_backend().open(doc.storage_key, doc.storage_url)
    except FileNotFoundError:
        raise Http404
    except requests.RequestException:
        return HttpResponseBadRequest("File storage is unavailable.")
    record(request.user, "DOCUMENT_DOWNLOADED", doc)
    resp = StreamingHttpResponse(stream, content_type=doc.mime_type)
    resp["Content-Disposition"] = content_disposition_header(True, doc.original_filename)
    resp["Content-Length"] = str(doc.size_bytes)
    resp["Cache-Control"] = "private, no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@login_required
@require_POST
def delete(request, pk):
    doc = get_object_or_404(Document, pk=pk, is_deleted=False)
    spec, owner = _owner_or_404(doc.owner_type, doc.owner_id)
    if not spec.can_attach(request.user, owner):
        raise PermissionDenied
    services.soft_delete(doc, request.user)
    messages.success(request, "Document removed.")
    return _back(request)
