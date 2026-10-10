"""RFQs raised from approved PRs, and the vendor quotations received against them."""
from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class RFQStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ISSUED = "ISSUED", "Issued"
    RESPONSES_CLOSED = "RESPONSES_CLOSED", "Responses closed"
    CANCELLED = "CANCELLED", "Cancelled"


class RFQ(TimeStampedModel):
    rfq_number = models.CharField(max_length=30, unique=True, editable=False)
    pr = models.ForeignKey("requisitions.PurchaseRequisition", on_delete=models.PROTECT, related_name="rfqs")
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=RFQStatus.choices, default=RFQStatus.DRAFT, db_index=True)
    response_deadline = models.DateTimeField()
    terms = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    issued_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "RFQ"

    def __str__(self):
        return self.rfq_number


class RFQLine(models.Model):
    rfq = models.ForeignKey(RFQ, on_delete=models.CASCADE, related_name="lines")
    pr_line = models.ForeignKey("requisitions.PRLine", on_delete=models.PROTECT, related_name="+")
    line_no = models.PositiveSmallIntegerField()
    description = models.CharField(max_length=300)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    uom = models.CharField(max_length=20)
    specs = models.TextField(blank=True)

    class Meta:
        ordering = ["line_no"]


class RFQVendor(models.Model):
    class Status(models.TextChoices):
        INVITED = "INVITED", "Invited"
        RESPONDED = "RESPONDED", "Responded"
        DECLINED = "DECLINED", "Declined"

    rfq = models.ForeignKey(RFQ, on_delete=models.CASCADE, related_name="rfq_vendors")
    vendor = models.ForeignKey("masterdata.Vendor", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.INVITED)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["rfq", "vendor"], name="rfq_vendor_unique")]


class QuotationStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Received"
    UNDER_REVIEW = "UNDER_REVIEW", "Under review"
    SHORTLISTED = "SHORTLISTED", "Shortlisted"
    REJECTED = "REJECTED", "Rejected"
    SUPERSEDED = "SUPERSEDED", "Superseded"


class Quotation(TimeStampedModel):
    quote_number = models.CharField(max_length=30, unique=True, editable=False)
    rfq = models.ForeignKey(RFQ, on_delete=models.PROTECT, related_name="quotations")
    vendor = models.ForeignKey("masterdata.Vendor", on_delete=models.PROTECT, related_name="+")
    revision_no = models.PositiveSmallIntegerField(default=1)
    vendor_ref_no = models.CharField(max_length=60, blank=True)
    quote_date = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    payment_terms = models.CharField(max_length=200, blank=True)
    lead_time_days = models.PositiveIntegerField(null=True, blank=True)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=15, choices=QuotationStatus.choices, default=QuotationStatus.RECEIVED)
    is_late = models.BooleanField(default=False)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["rfq", "vendor", "-revision_no"]
        constraints = [
            models.UniqueConstraint(fields=["rfq", "vendor", "revision_no"], name="quotation_revision_unique"),
            models.CheckConstraint(condition=models.Q(total_amount__gte=0), name="quotation_total_nonneg"),
        ]
        indexes = [models.Index(fields=["rfq", "vendor"])]

    def __str__(self):
        return self.quote_number


class QuotationLine(models.Model):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lines")
    rfq_line = models.ForeignKey(RFQLine, on_delete=models.PROTECT, related_name="+")
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    line_total = models.DecimalField(max_digits=18, decimal_places=2)
    delivery_days = models.PositiveIntegerField(null=True, blank=True)
    remarks = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["rfq_line__line_no"]
