"""
Purchase orders (POs).

Four ways a PO comes into being (`basis`):
  RFQ_AWARD              created from a finalised bid evaluation (competitive award)
  DIRECT_BELOW_THRESHOLD no RFQ needed: value <= policy threshold (aggregated per vendor/category)
  DIRECT_EXEMPTION       no RFQ, above threshold: needs an explicit CPO exemption approval
  RENEWAL                renews an earlier PO (RFQ-exempt unless the price moves beyond tolerance)
  RECURRING_RELEASE      generated automatically from an approved recurring agreement

A recurring purchase is a MASTER PO (`is_recurring`): approved once for its whole-term commitment,
then it spawns pre-authorised release POs on schedule (see schedule.py / services.generate_due_releases).
Every PO except releases is approved through the DoA rules whose doc_type is "PO".
"""
from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel

from .schedule import planned_cycles


class POStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    IN_APPROVAL = "IN_APPROVAL", "In approval"
    RETURNED = "RETURNED", "Returned"
    APPROVED = "APPROVED", "Approved"
    ISSUED = "ISSUED", "Issued"
    ACTIVE = "ACTIVE", "Active agreement"
    PAUSED = "PAUSED", "Paused"
    CLOSED = "CLOSED", "Closed"
    REJECTED = "REJECTED", "Rejected"
    CANCELLED = "CANCELLED", "Cancelled"


EDITABLE_STATUSES = (POStatus.DRAFT, POStatus.RETURNED)
LIVE_STATUSES = (POStatus.ISSUED, POStatus.ACTIVE, POStatus.PAUSED)


class SourcingBasis(models.TextChoices):
    RFQ_AWARD = "RFQ_AWARD", "RFQ award"
    DIRECT_BELOW_THRESHOLD = "DIRECT_BELOW_THRESHOLD", "Direct (below threshold)"
    DIRECT_EXEMPTION = "DIRECT_EXEMPTION", "Direct (approved exemption)"
    RENEWAL = "RENEWAL", "Renewal"
    RECURRING_RELEASE = "RECURRING_RELEASE", "Recurring release"


DIRECT_BASES = (SourcingBasis.DIRECT_BELOW_THRESHOLD, SourcingBasis.DIRECT_EXEMPTION)


class Frequency(models.TextChoices):
    WEEKLY = "WEEKLY", "Weekly"
    MONTHLY = "MONTHLY", "Monthly"
    QUARTERLY = "QUARTERLY", "Quarterly"
    YEARLY = "YEARLY", "Yearly"


class PurchasingPolicy(models.Model):
    """Singleton row edited by Admin in the console (Configuration -> Purchasing policy)."""

    direct_po_threshold = models.DecimalField(
        max_digits=18, decimal_places=2, default=5000,
        help_text="POs at or below this value may be raised without an RFQ.")
    aggregation_window_days = models.PositiveSmallIntegerField(
        default=30, help_text="Direct POs to the same vendor and category within this window are added together "
                              "when testing the threshold (prevents splitting a purchase to avoid an RFQ).")
    renewal_price_tolerance_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=5,
        help_text="A renewal whose value rises more than this % needs a CPO exemption approval.")
    renewal_lookahead_days = models.PositiveSmallIntegerField(
        default=60, help_text="The Renewals page lists agreements ending within this many days.")

    class Meta:
        verbose_name = "purchasing policy"
        verbose_name_plural = "purchasing policy"

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def __str__(self):
        return "Purchasing policy"


class PurchaseOrder(TimeStampedModel):
    po_number = models.CharField(max_length=30, unique=True, editable=False)
    title = models.CharField(max_length=200)
    vendor = models.ForeignKey("masterdata.Vendor", on_delete=models.PROTECT, related_name="+")
    department = models.ForeignKey("masterdata.Department", on_delete=models.PROTECT, related_name="+")
    category = models.ForeignKey("masterdata.Category", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=15, choices=POStatus.choices, default=POStatus.DRAFT, db_index=True)
    basis = models.CharField(max_length=25, choices=SourcingBasis.choices, blank=True, default="", db_index=True)

    # ---- provenance links (all optional)
    pr = models.ForeignKey("requisitions.PurchaseRequisition", null=True, blank=True, on_delete=models.PROTECT, related_name="purchase_orders")
    evaluation = models.ForeignKey("evaluation.Evaluation", null=True, blank=True, on_delete=models.PROTECT, related_name="purchase_orders")
    quotation = models.ForeignKey("sourcing.Quotation", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    renewal_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="renewals")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="releases",
                               help_text="Master agreement that generated this release.")

    # ---- commercial
    justification = models.TextField(blank=True, help_text="Why this is bought without an RFQ (direct purchases).")
    exemption_reason = models.TextField(blank=True, help_text="Required when the purchase needs a sourcing exemption.")
    exemption_required = models.BooleanField(default=False)
    sourcing_notes = models.TextField(blank=True, editable=False)
    payment_terms = models.CharField(max_length=200, blank=True)
    delivery_date = models.DateField(null=True, blank=True)
    charges_amount = models.DecimalField("Freight, taxes and other charges (net of discounts)", max_digits=18,
                                         decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0,
                                       help_text="Per PO (for a recurring agreement: per release).")

    # ---- recurring / term
    is_recurring = models.BooleanField(default=False)
    frequency = models.CharField(max_length=10, choices=Frequency.choices, blank=True, default="")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    schedule_index = models.PositiveSmallIntegerField(default=0, help_text="Next occurrence to generate.")
    scheduled_for = models.DateField(null=True, blank=True, help_text="Release date, for generated releases.")

    # ---- workflow
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_orders")
    doa_rule = models.ForeignKey("doa.DoARule", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    current_cycle = models.PositiveSmallIntegerField(default=0)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "basis"]), models.Index(fields=["vendor", "category"])]
        constraints = [
            models.CheckConstraint(condition=models.Q(total_amount__gte=0), name="po_total_nonneg"),
            models.CheckConstraint(
                condition=~models.Q(is_recurring=True) | models.Q(frequency__gt="", start_date__isnull=False, end_date__isnull=False),
                name="po_recurring_needs_schedule"),
        ]

    def __str__(self):
        return self.po_number or "New PO"

    @property
    def cycles(self) -> int:
        """Releases the agreement will produce (1 for a one-off PO)."""
        return planned_cycles(self.start_date, self.end_date, self.frequency) if self.is_recurring else 1

    @property
    def commitment_value(self) -> Decimal:
        """Whole-term value. This is what thresholds and the DoA are tested against."""
        return self.total_amount * self.cycles

    @property
    def is_editable(self):
        return self.status in EDITABLE_STATUSES


class POLine(models.Model):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveSmallIntegerField(default=0)
    description = models.CharField(max_length=300)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    uom = models.CharField("UoM", max_length=20, default="EA")
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    line_total = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)

    class Meta:
        ordering = ["line_no", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="poline_qty_pos"),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name="poline_price_nonneg"),
        ]

    def save(self, *args, **kwargs):
        self.line_total = (self.quantity * self.unit_price).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)


class POApprovalStep(models.Model):
    """One approval level of one submission cycle (snapshot of the DoA chain)."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        RETURNED = "RETURNED", "Returned"
        SKIPPED = "SKIPPED", "Skipped"

    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="approval_steps")
    cycle_no = models.PositiveSmallIntegerField()
    level = models.PositiveSmallIntegerField()
    required_role = models.CharField(max_length=20)
    covers_exemption = models.BooleanField(default=False, help_text="This approval also grants the sourcing exemption.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["cycle_no", "level"]
        constraints = [models.UniqueConstraint(fields=["po", "cycle_no", "level"], name="po_step_unique")]
        indexes = [models.Index(fields=["status", "required_role"])]
