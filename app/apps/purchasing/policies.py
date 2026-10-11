"""Who may see / edit / decide what for purchase orders."""
from django.db.models import F, Q

from apps.accounts.roles import GLOBAL_VIEW_ROLES, SOURCING_ROLES, Role

from .models import EDITABLE_STATUSES, POApprovalStep, POStatus, PurchaseOrder


def visible_pos(user):
    """Admin/CPO: all. Manager: own department (all if none set) + own POs. Specialist: own POs."""
    qs = PurchaseOrder.objects.select_related("vendor", "department", "category", "created_by")
    if user.role in GLOBAL_VIEW_ROLES:
        return qs
    if user.role == Role.MANAGER:
        return qs.filter(Q(department_id=user.department_id) | Q(created_by=user)) if user.department_id else qs
    return qs.filter(created_by=user)


def can_view_po(user, po) -> bool:
    return visible_pos(user).filter(pk=po.pk).exists()


def can_edit_po(user, po) -> bool:
    """Award POs take vendor/prices from the signed-off evaluation and releases are system generated."""
    return (po.created_by_id == user.pk and po.status in EDITABLE_STATUSES and user.role in SOURCING_ROLES
            and po.evaluation_id is None and po.parent_id is None)


def can_submit_po(user, po) -> bool:
    return po.created_by_id == user.pk and po.status in EDITABLE_STATUSES and user.role in SOURCING_ROLES


def can_operate_po(user) -> bool:
    """Issue / close / pause / resume / renew."""
    return user.role in SOURCING_ROLES


def decision_blocker(user, step: POApprovalStep):
    """None if `user` may decide `step` now, else a human-readable reason."""
    po = step.po
    if step.status != POApprovalStep.Status.PENDING or po.status != POStatus.IN_APPROVAL or step.cycle_no != po.current_cycle:
        return "This approval step is no longer open."
    if user.role != step.required_role:
        return f"This step requires the {step.required_role} role."
    if po.created_by_id == user.pk:
        return "You cannot approve a purchase order you created."
    if user.approval_limit is not None and po.commitment_value > user.approval_limit:
        return "The PO value exceeds your personal approval limit."
    if user.role == Role.MANAGER and user.department_id and user.department_id != po.department_id:
        return "This PO belongs to another department."
    if po.approval_steps.filter(cycle_no=step.cycle_no, level__lt=step.level).exclude(status=POApprovalStep.Status.APPROVED).exists():
        return "An earlier approval level must decide first."
    return None


def approval_inbox(user):
    if not user.can_approve:
        return []
    pending = POApprovalStep.objects.filter(
        status=POApprovalStep.Status.PENDING, required_role=user.role,
        po__status=POStatus.IN_APPROVAL, cycle_no=F("po__current_cycle"),
    ).select_related("po", "po__vendor", "po__department", "po__created_by").order_by("po__submitted_at")
    return [s for s in pending if decision_blocker(user, s) is None]
