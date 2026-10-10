"""
Delegation of Authority (DoA) rules. Data, not code: Admin maintains them in the console.

A rule matches on amount band + optional category + optional department and carries an
ordered chain of approver roles. The chain is SNAPSHOTTED onto each PR at submit time,
so later rule edits never change approvals already in flight.
"""
from django.core.exceptions import ValidationError
from django.db import models

from apps.accounts.roles import APPROVER_ROLES, Role
from apps.core.models import TimeStampedModel


class DoARule(TimeStampedModel):
    name = models.CharField(max_length=120)
    category = models.ForeignKey("masterdata.Category", null=True, blank=True, on_delete=models.CASCADE,
                                 help_text="Empty = any category.")
    department = models.ForeignKey("masterdata.Department", null=True, blank=True, on_delete=models.CASCADE,
                                   help_text="Empty = any department.")
    min_amount = models.DecimalField(max_digits=18, decimal_places=2)
    max_amount = models.DecimalField(max_digits=18, decimal_places=2)
    priority = models.PositiveIntegerField(default=100, help_text="Tie-breaker: lower wins.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["priority", "min_amount"]
        constraints = [
            models.CheckConstraint(condition=models.Q(min_amount__gte=0), name="doa_min_nonneg"),
            models.CheckConstraint(condition=models.Q(max_amount__gte=models.F("min_amount")), name="doa_max_ge_min"),
        ]

    def __str__(self):
        return f"{self.name} [{self.min_amount} - {self.max_amount}]"

    def clean(self):
        """Block overlapping active rules that have identical specificity (ambiguous matches)."""
        if self.min_amount is None or self.max_amount is None:
            return
        if self.min_amount > self.max_amount:
            raise ValidationError("Minimum amount cannot exceed maximum amount.")
        if self.is_active:
            clash = DoARule.objects.filter(
                is_active=True, category=self.category, department=self.department,
                min_amount__lte=self.max_amount, max_amount__gte=self.min_amount,
            ).exclude(pk=self.pk)
            if clash.exists():
                raise ValidationError(f"Overlaps an existing rule with the same scope: {clash.first()}")


class DoARuleStep(models.Model):
    rule = models.ForeignKey(DoARule, on_delete=models.CASCADE, related_name="steps")
    level = models.PositiveSmallIntegerField(help_text="1 = first approver.")
    approver_role = models.CharField(max_length=20, choices=[(r.value, r.label) for r in APPROVER_ROLES],
                                     default=Role.MANAGER)

    class Meta:
        ordering = ["level"]
        constraints = [models.UniqueConstraint(fields=["rule", "level"], name="doa_unique_level")]

    def __str__(self):
        return f"L{self.level}: {self.approver_role}"
