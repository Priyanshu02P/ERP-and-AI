"""Registers RFQs and quotations as document owner types."""
from apps.documents.registry import OwnerType, register

from .models import RFQ, Quotation
from .policies import can_attach_to_rfq, can_manage_rfq, can_view_rfq

register(OwnerType(
    code="RFQ", label="RFQ", default_kind="RFQ_PACK",
    load=lambda pk: RFQ.objects.filter(pk=pk).first(),
    reference=lambda rfq: rfq.rfq_number,
    can_view=can_view_rfq, can_attach=can_attach_to_rfq,
))

register(OwnerType(
    code="QUOTATION", label="Quotation", default_kind="QUOTATION_PDF",
    load=lambda pk: Quotation.objects.select_related("rfq").filter(pk=pk).first(),
    reference=lambda q: q.quote_number,
    can_view=lambda user, q: can_view_rfq(user, q.rfq),
    can_attach=lambda user, q: can_manage_rfq(user),
))
