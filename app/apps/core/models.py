"""Cross-cutting base models."""
from django.db import models


class TimeStampedModel(models.Model):
    """Adds created_at / updated_at to a model."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class NumberSequence(models.Model):
    """Counter row per document type and year, e.g. key='PR-2026'. See numbering.py."""

    key = models.CharField(max_length=40, unique=True)
    next_value = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.key}: {self.next_value}"
