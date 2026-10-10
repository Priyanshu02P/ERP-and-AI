"""Purchase requisition (PR), its lines and its DoA approval steps."""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class PRStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    IN_APPROVAL = "IN_APPROVAL", "In approval"
    RETURNED = "RETURNED", "Returned"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    RFQ_CREATED = "RFQ_CREATED", "RFQ created"
    CANCELLED = "CANCELLED", "Cancelled"
    CLOSED = "CLOSED", "Closed"


EDITABLE_STATUSES = (PRStatus.DRAFT, PRStatus.RETURNED)


class StepStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    RETURNED = "RETURNED", "Returned"
    SKIPPED = "SKIPPED", "Skipped"


class PurchaseRequisition(TimeStampedModel):
    pr_number = models.CharField(max_length=30, unique=True, editable=False)
    title = models.CharField(max_length=200)
    justification = models.TextField(blank=True)
    department = models.ForeignKey("masterdata.Department", on_delete=models.PROTECT)
    category = models.ForeignKey("masterdata.Category", on_delete=models.PROTECT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requisitions")
    needed_by = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=PRStatus.choices, default=PRStatus.DRAFT, db_index=True)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    doa_rule = models.ForeignKey("doa.DoARule", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    current_cycle = models.PositiveSmallIntegerField(default=0, help_text="Increments on every (re)submission.")
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "department"])]
        constraints = [models.CheckConstraint(condition=models.Q(total_amount__gte=0), name="pr_total_nonneg")]

    def __str__(self):
        return self.pr_number or "New PR"

    @property
    def is_editable(self):
        return self.status in EDITABLE_STATUSES


class PRLine(models.Model):
    pr = models.ForeignKey(PurchaseRequisition, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveSmallIntegerField(default=0)
    description = models.CharField(max_length=300)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    uom = models.CharField("UoM", max_length=20, default="EA")
    est_unit_price = models.DecimalField("Est. unit price", max_digits=18, decimal_places=2)
    est_total = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)

    class Meta:
        ordering = ["line_no", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="prline_qty_pos"),
            models.CheckConstraint(condition=models.Q(est_unit_price__gte=0), name="prline_price_nonneg"),
        ]

    def save(self, *args, **kwargs):
        self.est_total = (self.quantity * self.est_unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.pr_id}/{self.line_no}"


class PRApprovalStep(models.Model):
    """One approval level of one submission cycle (snapshot of the DoA chain)."""

    pr = models.ForeignKey(PurchaseRequisition, on_delete=models.CASCADE, related_name="approval_steps")
    cycle_no = models.PositiveSmallIntegerField()
    level = models.PositiveSmallIntegerField()
    required_role = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=StepStatus.choices, default=StepStatus.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["cycle_no", "level"]
        constraints = [models.UniqueConstraint(fields=["pr", "cycle_no", "level"], name="pr_step_unique")]
        indexes = [models.Index(fields=["status", "required_role"])]

    def __str__(self):
        return f"{self.pr_id} c{self.cycle_no} L{self.level} {self.status}"
