"""`python manage.py seed_demo` - demo master data, DoA rules and one user per role."""
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.doa.models import DoARule, DoARuleStep
from apps.masterdata.models import Category, Department, Vendor

MAX = Decimal("999999999999.99")


class Command(BaseCommand):
    help = "Create demo departments, categories, vendors, DoA rules and users (idempotent)."

    def handle(self, *args, **options):
        it, _ = Department.objects.get_or_create(code="IT", defaults={"name": "Information Technology"})
        Department.objects.get_or_create(code="OPS", defaults={"name": "Operations"})
        Category.objects.get_or_create(code="GOODS", defaults={"name": "Goods"})
        capex, _ = Category.objects.get_or_create(code="CAPEX", defaults={"name": "Capital Expenditure", "is_capex": True})
        for code, name in [("V001", "Acme Supplies"), ("V002", "Globex Trading"), ("V003", "Initech Services")]:
            Vendor.objects.get_or_create(code=code, defaults={"name": name, "email": f"{code.lower()}@example.com"})

        rules = [
            ("Up to 10k", None, "0", "10000", [Role.MANAGER]),
            ("10k to 100k", None, "10000.01", "100000", [Role.MANAGER, Role.CPO]),
            ("Above 100k", None, "100000.01", MAX, [Role.MANAGER, Role.CPO]),
            ("CapEx (always CPO)", capex, "0", MAX, [Role.MANAGER, Role.CPO]),
        ]
        for name, cat, lo, hi, chain in rules:
            rule, created = DoARule.objects.get_or_create(
                name=name, defaults={"category": cat, "min_amount": Decimal(lo), "max_amount": Decimal(hi)})
            if created:
                for level, role in enumerate(chain, 1):
                    DoARuleStep.objects.create(rule=rule, level=level, approver_role=role)

        for username, role in [("specialist1", Role.SPECIALIST), ("manager1", Role.MANAGER), ("cpo1", Role.CPO)]:
            user, created = User.objects.get_or_create(username=username, defaults={"role": role, "department": it})
            if created:
                user.set_password("Demo@12345")
                user.save()
        self.stdout.write(self.style.SUCCESS("Demo data ready. Demo users share password 'Demo@12345'."))
