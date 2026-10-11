"""Lets users attach contracts, signed POs and vendor acknowledgements to a PO."""
from apps.documents.registry import OwnerType, register

from .models import POStatus, PurchaseOrder
from .policies import can_operate_po, can_view_po

_ATTACHABLE = (POStatus.DRAFT, POStatus.RETURNED, POStatus.APPROVED, POStatus.ISSUED, POStatus.ACTIVE, POStatus.PAUSED)

register(OwnerType(
    code="PO", label="Purchase order", default_kind="PO_DOCUMENT",
    load=lambda pk: PurchaseOrder.objects.filter(pk=pk).first(),
    reference=lambda po: po.po_number,
    can_view=can_view_po,
    can_attach=lambda user, po: can_operate_po(user) and po.status in _ATTACHABLE,
))
