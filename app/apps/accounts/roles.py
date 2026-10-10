"""Role definitions and role groups. Import from here instead of hard-coding strings."""
from django.db import models


class Role(models.TextChoices):
    SPECIALIST = "SPECIALIST", "Procurement Specialist"
    MANAGER = "MANAGER", "Procurement Manager"
    CPO = "CPO", "Chief Procurement Officer"
    ADMIN = "ADMIN", "Admin"


# Who may raise / edit / submit purchase requisitions.
PR_CREATOR_ROLES = (Role.SPECIALIST, Role.MANAGER)
# Who may sit in a DoA approval chain.
APPROVER_ROLES = (Role.MANAGER, Role.CPO)
# Who may create/issue RFQs and record quotations.
SOURCING_ROLES = (Role.SPECIALIST, Role.MANAGER)
# Roles with organisation-wide read access.
GLOBAL_VIEW_ROLES = (Role.CPO, Role.ADMIN)
