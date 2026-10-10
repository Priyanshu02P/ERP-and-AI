"""Reference data maintained by Admin (via the Django admin console)."""
from django.db import models

from apps.core.models import TimeStampedModel


class Department(TimeStampedModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Category(TimeStampedModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    is_capex = models.BooleanField(default=False, help_text="Capital expenditure (typically stricter DoA).")

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "categories"

    def __str__(self):
        return f"{self.code} - {self.name}"


class Vendor(TimeStampedModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    tax_id = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True, help_text="Untick to block the vendor from new RFQs.")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} - {self.name}"
