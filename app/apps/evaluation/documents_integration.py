"""Lets evaluators attach evidence (scoring sheets, certificates, emails) to an evaluation."""
from apps.documents.registry import OwnerType, register
from apps.sourcing.policies import can_manage_rfq, can_view_rfq

from .models import Evaluation, EvaluationStatus

register(OwnerType(
    code="EVALUATION", label="Evaluation", default_kind="EVALUATION_EVIDENCE",
    load=lambda pk: Evaluation.objects.select_related("rfq__pr").filter(pk=pk).first(),
    reference=lambda ev: ev.rfq.rfq_number,
    can_view=lambda user, ev: can_view_rfq(user, ev.rfq),
    can_attach=lambda user, ev: can_manage_rfq(user) and ev.status == EvaluationStatus.DRAFT,
))
