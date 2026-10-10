"""DoA resolver: amount boundaries, specificity, overlap validation."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.roles import Role
from apps.doa.exceptions import NoDoARuleError
from apps.doa.models import DoARule, DoARuleStep
from apps.doa.resolver import DoAResolver
from apps.masterdata.models import Category


def make_rule(name, lo, hi, roles, category=None):
    rule = DoARule.objects.create(name=name, min_amount=Decimal(lo), max_amount=Decimal(hi), category=category)
    for i, r in enumerate(roles, 1):
        DoARuleStep.objects.create(rule=rule, level=i, approver_role=r)
    return rule


class ResolverTests(TestCase):
    def setUp(self):
        self.capex = Category.objects.create(code="CX", name="Capex", is_capex=True)
        self.low = make_rule("low", "0", "10000", [Role.MANAGER])
        self.high = make_rule("high", "10000.01", "100000", [Role.MANAGER, Role.CPO])
        self.capex_rule = make_rule("capex", "0", "999999", [Role.MANAGER, Role.CPO], self.capex)

    def test_boundaries(self):
        self.assertEqual(DoAResolver.resolve(Decimal("10000")).pk, self.low.pk)
        self.assertEqual(DoAResolver.resolve(Decimal("10000.01")).pk, self.high.pk)

    def test_category_specific_rule_wins(self):
        self.assertEqual(DoAResolver.resolve(Decimal("50"), self.capex).pk, self.capex_rule.pk)

    def test_no_rule(self):
        with self.assertRaises(NoDoARuleError):
            DoAResolver.resolve(Decimal("500000"))

    def test_overlap_rejected(self):
        clash = DoARule(name="clash", min_amount=Decimal("5000"), max_amount=Decimal("20000"))
        with self.assertRaises(ValidationError):
            clash.clean()
