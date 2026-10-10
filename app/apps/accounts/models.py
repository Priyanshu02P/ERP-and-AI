"""Custom user model: one `role` per user, assigned by Admin."""
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

from .roles import APPROVER_ROLES, PR_CREATOR_ROLES, SOURCING_ROLES, Role


class User(AbstractUser):
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.SPECIALIST, db_index=True)
    department = models.ForeignKey(
        "masterdata.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="users"
    )
    approval_limit = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Optional personal cap on the PR value this user may approve. Empty = no personal cap.",
    )

    def save(self, *args, **kwargs):
        # Single source of truth: Django-admin access follows the Admin role.
        self.is_staff = self.is_superuser = self.role == Role.ADMIN
        super().save(*args, **kwargs)

    # ---- convenience flags used by templates and views
    @property
    def is_hardcoded_admin(self) -> bool:
        return self.username == settings.HARDCODED_ADMIN["username"]

    @property
    def is_admin_role(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def can_approve(self) -> bool:
        return self.role in APPROVER_ROLES

    @property
    def can_create_pr(self) -> bool:
        return self.role in PR_CREATOR_ROLES

    @property
    def can_source(self) -> bool:
        return self.role in SOURCING_ROLES
