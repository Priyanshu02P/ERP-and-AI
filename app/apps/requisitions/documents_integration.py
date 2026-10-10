"""Registers purchase requisitions as an owner type for file attachments."""
from apps.documents.registry import OwnerType, register

from .models import PurchaseRequisition
from .policies import can_edit_pr, can_view_pr

register(OwnerType(
    code="PR",
    label="Purchase requisition",
    default_kind="PR_ATTACHMENT",
    load=lambda pk: PurchaseRequisition.objects.filter(pk=pk).first(),
    reference=lambda pr: pr.pr_number,
    can_view=can_view_pr,
    can_attach=can_edit_pr,  # attachments only while the PR is editable
))
