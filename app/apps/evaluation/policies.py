"""Who may do what with an evaluation."""
from apps.accounts.roles import Role

from .models import EvaluationStatus


def review_blocker(user, evaluation):
    """None if `user` may sign off / return the evaluation, else a reason (four-eyes principle)."""
    if evaluation.status != EvaluationStatus.UNDER_REVIEW:
        return "This evaluation is not awaiting review."
    if user.role not in (Role.MANAGER, Role.CPO):
        return "Only a Procurement Manager or the CPO can review an evaluation."
    if evaluation.submitted_by_id == user.pk:
        return "You submitted this evaluation, so someone else must review it."
    pr = evaluation.rfq.pr
    if user.role == Role.MANAGER and user.department_id and user.department_id != pr.department_id:
        return "This RFQ belongs to another department."
    return None
