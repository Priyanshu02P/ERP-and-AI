"""Who may see / edit / decide what. Pure functions + querysets; no side effects."""
from django.db.models import F, Q

from apps.accounts.roles import GLOBAL_VIEW_ROLES, PR_CREATOR_ROLES, Role

from .models import EDITABLE_STATUSES, PRApprovalStep, PRStatus, PurchaseRequisition, StepStatus


def visible_prs(user):
    """
    Admin / CPO: all.  Manager: own department (all if no department set) + own PRs.
    Specialist: own PRs + approved PRs awaiting sourcing (so they can raise RFQs).
    """
    qs = PurchaseRequisition.objects.select_related("department", "category", "requested_by")
    if user.role in GLOBAL_VIEW_ROLES:
        return qs
    if user.role == Role.MANAGER:
        if not user.department_id:
            return qs
        return qs.filter(Q(department_id=user.department_id) | Q(requested_by=user))
    return qs.filter(Q(requested_by=user) | Q(status__in=[PRStatus.APPROVED, PRStatus.RFQ_CREATED]))


def can_view_pr(user, pr) -> bool:
    return visible_prs(user).filter(pk=pr.pk).exists()


def can_edit_pr(user, pr) -> bool:
    return pr.requested_by_id == user.pk and pr.status in EDITABLE_STATUSES and user.role in PR_CREATOR_ROLES


def decision_blocker(user, step: PRApprovalStep):
    """Return None if `user` may decide `step` now, else a human-readable reason."""
    pr = step.pr
    if step.status != StepStatus.PENDING or pr.status != PRStatus.IN_APPROVAL or step.cycle_no != pr.current_cycle:
        return "This approval step is no longer open."
    if user.role != step.required_role:
        return f"This step requires the {step.required_role} role."
    if pr.requested_by_id == user.pk:
        return "You cannot approve your own requisition."
    if user.approval_limit is not None and pr.total_amount > user.approval_limit:
        return "The PR value exceeds your personal approval limit."
    if user.role == Role.MANAGER and user.department_id and user.department_id != pr.department_id:
        return "This PR belongs to another department."
    earlier_open = pr.approval_steps.filter(cycle_no=step.cycle_no, level__lt=step.level).exclude(
        status=StepStatus.APPROVED).exists()
    if earlier_open:
        return "An earlier approval level must decide first."
    return None


def approval_inbox(user):
    """Open steps the user can act on right now (earlier levels done, not their own PR, within limit)."""
    if not user.can_approve:
        return []
    pending = PRApprovalStep.objects.filter(
        status=StepStatus.PENDING, required_role=user.role,
        pr__status=PRStatus.IN_APPROVAL, cycle_no=F("pr__current_cycle"),
    ).select_related("pr", "pr__department", "pr__category", "pr__requested_by").order_by("pr__submitted_at")
    return [s for s in pending if decision_blocker(user, s) is None]
