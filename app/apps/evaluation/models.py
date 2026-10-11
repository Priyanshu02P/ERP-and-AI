"""
Bid evaluation: mandatory gates first, then a weighted scorecard for eligible bidders.

One Evaluation per RFQ. It evaluates the CURRENT quotation of each invited vendor
(superseded revisions and rejected quotes are excluded). Workflow:
    DRAFT (evaluator edits) -> UNDER_REVIEW (submitted) -> FINALIZED (reviewer signs off)
or the reviewer returns it to DRAFT. On finalisation the computed results are frozen in
`result_snapshot`, so later data changes can never alter a signed-off evaluation.
"""
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from apps.core.models import TimeStampedModel


class Criterion(models.TextChoices):
    COST = "COST", "Evaluated total cost"
    QUALITY = "QUALITY", "Quality and technical fit"
    DELIVERY = "DELIVERY", "Delivery capability and reliability"
    COMPLIANCE = "COMPLIANCE", "Compliance and documentation"
    RISK = "RISK", "Supplier risk and resilience"
    SERVICE = "SERVICE", "Warranty and service"


# COST is computed from prices; the rest are scored 0-100 by the evaluator.
MANUAL_CRITERIA = [c for c in Criterion.values if c != Criterion.COST]

# Illustrative defaults - override per evaluation (must total 100) to suit the category/policy.
DEFAULT_WEIGHTS = {"COST": 30, "QUALITY": 25, "DELIVERY": 20, "COMPLIANCE": 10, "RISK": 10, "SERVICE": 5}


def default_weights():
    return dict(DEFAULT_WEIGHTS)


class EvaluationStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    UNDER_REVIEW = "UNDER_REVIEW", "Under review"
    FINALIZED = "FINALIZED", "Finalised"


class Evaluation(TimeStampedModel):
    rfq = models.OneToOneField("sourcing.RFQ", on_delete=models.PROTECT, related_name="evaluation")
    status = models.CharField(max_length=15, choices=EvaluationStatus.choices, default=EvaluationStatus.DRAFT, db_index=True)
    weights = models.JSONField(default=default_weights)
    recommended_entry = models.ForeignKey("EvaluationEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    rationale = models.TextField(blank=True, help_text="Why this supplier is recommended; mandatory before submission.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True)
    result_snapshot = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)

    def __str__(self):
        return f"Evaluation of {self.rfq.rfq_number}"


class EvaluationEntry(models.Model):
    """One bidder (one quotation revision) inside an evaluation."""

    evaluation = models.ForeignKey(Evaluation, on_delete=models.CASCADE, related_name="entries")
    quotation = models.ForeignKey("sourcing.Quotation", on_delete=models.PROTECT, related_name="+")
    # Mandatory pass/fail gates, confirmed by the evaluator. None = not yet assessed (blocks submission).
    gate_specs = models.BooleanField("Meets mandatory specifications", null=True, blank=True)
    gate_certificates = models.BooleanField("Required certificates available", null=True, blank=True)
    gate_capacity = models.BooleanField("Capacity confirmed for required quantity", null=True, blank=True)
    is_disqualified = models.BooleanField(default=False)
    disqualification_reason = models.CharField(max_length=300, blank=True)
    # Documented assumptions that make quotes comparable (inspection, expected defects, financing ...).
    adjustment_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    adjustment_note = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["evaluation", "quotation"], name="eval_entry_unique")]
        ordering = ["id"]
        verbose_name_plural = "evaluation entries"

    def __str__(self):
        return f"{self.evaluation_id}/{self.quotation_id}"


class EvaluationScore(models.Model):
    entry = models.ForeignKey(EvaluationEntry, on_delete=models.CASCADE, related_name="scores")
    criterion = models.CharField(max_length=15, choices=[(c, c) for c in MANUAL_CRITERIA])
    score = models.DecimalField(max_digits=5, decimal_places=2)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["entry", "criterion"], name="eval_score_unique"),
            models.CheckConstraint(condition=models.Q(score__gte=0, score__lte=100), name="eval_score_range"),
        ]
