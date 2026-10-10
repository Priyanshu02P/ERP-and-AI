"""
PR business logic. Views call ONLY these functions to change state.

Every transition runs in a transaction and locks the PR row (SELECT ... FOR UPDATE) so
double-clicks and concurrent approvers cannot corrupt state.
"""
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.services import record
from apps.core.exceptions import WorkflowError
from apps.core.numbering import next_number
from apps.doa.resolver import DoAResolver

from .models import (EDITABLE_STATUSES, PRApprovalStep, PRStatus, PurchaseRequisition, StepStatus)
from .policies import decision_blocker

DECISIONS = (StepStatus.APPROVED, StepStatus.REJECTED, StepStatus.RETURNED)


def _lock(pr_id) -> PurchaseRequisition:
    return PurchaseRequisition.objects.select_for_update().get(pk=pr_id)


def recalculate_totals(pr) -> None:
    pr.total_amount = pr.lines.aggregate(s=Sum("est_total"))["s"] or 0
    pr.save(update_fields=["total_amount", "updated_at"])


@transaction.atomic
def save_pr_with_lines(form, formset, user) -> PurchaseRequisition:
    """Persist a validated PR form + line formset (create or edit), renumber lines, refresh total."""
    pr = form.save(commit=False)
    creating = pr.pk is None
    if creating:
        pr.requested_by = user
        pr.pr_number = next_number("PR")
    pr.save()
    formset.save()
    for i, line in enumerate(pr.lines.order_by("id"), start=1):
        if line.line_no != i:
            line.line_no = i
            line.save(update_fields=["line_no"])
    recalculate_totals(pr)
    record(user, "PR_CREATED" if creating else "PR_UPDATED", pr, data={"total": pr.total_amount})
    return pr


@transaction.atomic
def submit_pr(pr_id, user) -> PurchaseRequisition:
    """DRAFT/RETURNED -> IN_APPROVAL. Resolves the DoA rule and snapshots its chain."""
    pr = _lock(pr_id)
    if pr.requested_by_id != user.pk:
        raise WorkflowError("Only the requester can submit this PR.")
    if pr.status not in EDITABLE_STATUSES:
        raise WorkflowError(f"A PR in status {pr.get_status_display()} cannot be submitted.")
    recalculate_totals(pr)
    if not pr.lines.exists() or pr.total_amount <= 0:
        raise WorkflowError("Add at least one line with a value greater than zero.")

    rule = DoAResolver.resolve(pr.total_amount, pr.category, pr.department)
    cycle = pr.current_cycle + 1
    PRApprovalStep.objects.bulk_create(
        PRApprovalStep(pr=pr, cycle_no=cycle, level=s.level, required_role=s.approver_role) for s in rule.steps.all()
    )
    pr.status, pr.current_cycle, pr.doa_rule, pr.submitted_at = PRStatus.IN_APPROVAL, cycle, rule, timezone.now()
    pr.save()
    record(user, "PR_SUBMITTED", pr, data={"rule": rule.name, "cycle": cycle, "total": pr.total_amount})
    return pr


@transaction.atomic
def decide(step_id, user, decision, comment="") -> PurchaseRequisition:
    """Approve, reject or return one approval step, enforcing every DoA control."""
    if decision not in DECISIONS:
        raise WorkflowError("Unknown decision.")
    comment = (comment or "").strip()
    if decision in (StepStatus.REJECTED, StepStatus.RETURNED) and not comment:
        raise WorkflowError("A comment is required when rejecting or returning a PR.")

    pr_id = PRApprovalStep.objects.values_list("pr_id", flat=True).get(pk=step_id)
    pr = _lock(pr_id)  # lock first, then re-read the step so we see the latest state
    step = PRApprovalStep.objects.select_related("pr").get(pk=step_id)
    step.pr = pr
    blocker = decision_blocker(user, step)
    if blocker:
        raise WorkflowError(blocker)

    step.status, step.decided_by, step.decided_at, step.comment = decision, user, timezone.now(), comment
    step.save()

    if decision == StepStatus.APPROVED:
        still_open = pr.approval_steps.filter(cycle_no=step.cycle_no, status=StepStatus.PENDING).exists()
        if not still_open:
            pr.status, pr.approved_at = PRStatus.APPROVED, timezone.now()
    else:
        pr.approval_steps.filter(cycle_no=step.cycle_no, status=StepStatus.PENDING).update(status=StepStatus.SKIPPED)
        pr.status = PRStatus.REJECTED if decision == StepStatus.REJECTED else PRStatus.RETURNED
    pr.save()
    record(user, f"PR_{decision}", pr, data={"level": step.level, "comment": comment})
    return pr


@transaction.atomic
def cancel_pr(pr_id, user) -> PurchaseRequisition:
    pr = _lock(pr_id)
    if pr.requested_by_id != user.pk:
        raise WorkflowError("Only the requester can cancel this PR.")
    if pr.status not in (PRStatus.DRAFT, PRStatus.RETURNED, PRStatus.IN_APPROVAL):
        raise WorkflowError(f"A PR in status {pr.get_status_display()} cannot be cancelled.")
    pr.approval_steps.filter(status=StepStatus.PENDING).update(status=StepStatus.SKIPPED)
    pr.status = PRStatus.CANCELLED
    pr.save()
    record(user, "PR_CANCELLED", pr)
    return pr
