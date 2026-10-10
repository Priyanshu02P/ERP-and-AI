"""
Document use-cases: validate -> upload to Blob -> record metadata in Neon.

Upload and DB write cannot share a transaction, so callers that also write other rows
(e.g. quotations) use the two-phase helpers and call `discard_stored` if the DB part fails.
"""
import hashlib
import mimetypes
import os
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import get_valid_filename

from apps.audit.services import record
from apps.core.exceptions import WorkflowError

from . import registry
from .models import Document
from .storage import get_backend


@dataclass(frozen=True)
class StoredFile:
    key: str
    url: str
    filename: str
    mime_type: str
    size: int
    sha256: str


def validate_upload(upload) -> None:
    ext = os.path.splitext(upload.name)[1].lower()
    if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(settings.ALLOWED_UPLOAD_EXTENSIONS))
        raise WorkflowError(f"File type {ext or '(none)'} is not allowed. Allowed: {allowed}.")
    if upload.size == 0:
        raise WorkflowError("The file is empty.")
    if upload.size > settings.MAX_UPLOAD_BYTES:
        raise WorkflowError(f"File is larger than {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")


def upload_to_storage(upload, prefix: str) -> StoredFile:
    """Phase 1: validate and push the bytes to Blob. `prefix` e.g. 'quotations/RFQ-2026-000001'."""
    validate_upload(upload)
    data = upload.read()
    safe_name = get_valid_filename(os.path.basename(upload.name))
    key = f"{prefix}/{timezone.now():%Y}/{uuid.uuid4().hex}_{safe_name}"
    mime = upload.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    blob = get_backend().put(key, data, mime)
    return StoredFile(blob.key, blob.url, safe_name, mime, len(data), hashlib.sha256(data).hexdigest())


def register_document(stored: StoredFile, owner_type: str, owner_id: int, kind: str, user) -> Document:
    """Phase 2: write the metadata row (call inside the caller's transaction)."""
    doc = Document.objects.create(
        owner_type=owner_type, owner_id=owner_id, kind=kind, storage_key=stored.key, storage_url=stored.url,
        original_filename=stored.filename, mime_type=stored.mime_type, size_bytes=stored.size,
        sha256=stored.sha256, uploaded_by=user,
    )
    record(user, "DOCUMENT_UPLOADED", doc, data={"owner": f"{owner_type}:{owner_id}", "kind": kind, "sha256": stored.sha256})
    return doc


def discard_stored(stored: StoredFile) -> None:
    """Best-effort cleanup of an orphaned blob after a failed DB write."""
    try:
        get_backend().delete(stored.key, stored.url)
    except Exception:  # noqa: BLE001 - cleanup must never mask the original error
        pass


def attach_file(upload, owner_type: str, owner, user) -> Document:
    """Upload a file and attach it to `owner` (a registered owner type)."""
    spec = registry.get(owner_type)
    stored = upload_to_storage(upload, f"{spec.label.lower().replace(' ', '-')}/{spec.reference(owner)}")
    try:
        with transaction.atomic():
            return register_document(stored, owner_type, owner.pk, spec.default_kind, user)
    except Exception:
        discard_stored(stored)
        raise


def list_documents(owner_type: str, owner_id: int):
    return Document.objects.filter(owner_type=owner_type, owner_id=owner_id, is_deleted=False).select_related("uploaded_by")


@transaction.atomic
def soft_delete(doc: Document, user) -> None:
    doc.is_deleted = True
    doc.save(update_fields=["is_deleted"])
    record(user, "DOCUMENT_DELETED", doc)
