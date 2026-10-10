"""Visibility and permission rules for RFQs and quotations."""
from django.db.models import Q

from apps.accounts.roles import GLOBAL_VIEW_ROLES, SOURCING_ROLES, Role

from .models import RFQ, RFQStatus


def visible_rfqs(user):
    qs = RFQ.objects.select_related("pr", "created_by")
    if user.role in GLOBAL_VIEW_ROLES:
        return qs
    if user.role == Role.MANAGER and user.department_id:
        return qs.filter(Q(pr__department_id=user.department_id) | Q(created_by=user))
    if user.role == Role.MANAGER:
        return qs
    return qs.filter(Q(created_by=user) | Q(pr__requested_by=user))


def can_view_rfq(user, rfq) -> bool:
    return visible_rfqs(user).filter(pk=rfq.pk).exists()


def can_manage_rfq(user, rfq=None) -> bool:
    return user.role in SOURCING_ROLES


def can_attach_to_rfq(user, rfq) -> bool:
    return can_manage_rfq(user) and rfq.status in (RFQStatus.DRAFT, RFQStatus.ISSUED, RFQStatus.RESPONSES_CLOSED)
