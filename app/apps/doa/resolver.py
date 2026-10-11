"""Finds the DoA rule that governs a given amount/category/department."""
from decimal import Decimal

from django.db.models import Case, IntegerField, Q, Value, When

from .exceptions import NoDoARuleError
from .models import DoARule


class DoAResolver:
    @staticmethod
    def resolve(amount: Decimal, category=None, department=None, doc_type: str = "PR") -> DoARule:
        """
        Pick the best matching rule:
          0. rule is for this document type (PR or PO)
          1. amount within [min, max]
          2. category/department equal OR unset (unset = wildcard)
          3. most specific first (category + department > one > none), then lowest `priority`
        Raises NoDoARuleError if nothing matches or the rule has no approval steps.
        """
        qs = DoARule.objects.filter(is_active=True, doc_type=doc_type, min_amount__lte=amount, max_amount__gte=amount)
        qs = qs.filter(Q(category=category) | Q(category__isnull=True))
        qs = qs.filter(Q(department=department) | Q(department__isnull=True))
        qs = qs.annotate(
            specificity=Case(When(category__isnull=False, then=Value(1)), default=Value(0), output_field=IntegerField())
            + Case(When(department__isnull=False, then=Value(1)), default=Value(0), output_field=IntegerField())
        ).order_by("-specificity", "priority", "id")
        rule = qs.prefetch_related("steps").first()
        if rule is None:
            raise NoDoARuleError(f"No DoA rule covers this {doc_type}. Ask an Admin to configure one.")
        if not rule.steps.all():
            raise NoDoARuleError(f"DoA rule '{rule.name}' has no approval steps. Ask an Admin to fix it.")
        return rule
