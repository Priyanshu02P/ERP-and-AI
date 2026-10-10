"""Metadata for files kept in Vercel Blob. The bytes never live in Postgres."""
from django.conf import settings
from django.db import models


class Document(models.Model):
    owner_type = models.CharField(max_length=20, help_text="Registry code: PR, RFQ, QUOTATION ...")
    owner_id = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=30)
    storage_key = models.CharField(max_length=500, unique=True, help_text="Blob pathname.")
    storage_url = models.CharField(max_length=1000, help_text="Blob URL (private; needs the token to read).")
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)  # soft delete only

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [models.Index(fields=["owner_type", "owner_id"])]

    def __str__(self):
        return self.original_filename
