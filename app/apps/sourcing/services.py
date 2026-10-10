"""
RFQ and quotation business logic.

Flow: approved PR -> create_rfq_from_pr (DRAFT) -> issue_rfq (ISSUED) -> record_quotation
(DB row + file in Blob) -> close_rfq / close_expired_rfqs (RESPONSES_CLOSED).
"""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.core.exceptions import WorkflowError
from apps.core.numbering import next_number
from apps.documents import services as doc_services
from apps.requisitions.models import PRStatus, PurchaseRequisition

from .models import (RFQ, Quotation, QuotationLine, QuotationStatus, RFQLine, RFQStatus, RFQVendor)

_CENT = Decimal("0.01")


# --------------------------------------------------------------------------- RFQ lifecycle
@transaction.atomic
def create_rfq_from_pr(pr_id, user, vendors, deadline, title="", terms="") -> RFQ:
    """Create a DRAFT RFQ from an APPROVED PR: copies lines and invites the chosen vendors."""
    pr = PurchaseRequisition.objects.select_for_update().get(pk=pr_id)
    allowed = {PRStatus.APPROVED} | ({PRStatus.RFQ_CREATED} if settings.ALLOW_MULTIPLE_RFQ_PER_PR else set())
    if pr.status not in allowed:
        raise WorkflowError("An RFQ can only be created from an approved PR that has no RFQ yet.")
    vendors = [v for v in vendors if v.is_active]
    if not vendors:
        raise WorkflowError("Select at least one active vendor.")
    if deadline <= timezone.now():
        raise WorkflowError("The response deadline must be in the future.")

    rfq = RFQ.objects.create(rfq_number=next_number("RFQ"), pr=pr, title=title or pr.title,
                             response_deadline=deadline, terms=terms, created_by=user)
    RFQLine.objects.bulk_create(
        RFQLine(rfq=rfq, pr_line=l, line_no=l.line_no, description=l.description, quantity=l.quantity, uom=l.uom)
        for l in pr.lines.all()
    )
    RFQVendor.objects.bulk_create(RFQVendor(rfq=rfq, vendor=v) for v in vendors)
    pr.status = PRStatus.RFQ_CREATED
    pr.save(update_fields=["status", "updated_at"])
    record(user, "RFQ_CREATED", rfq, data={"pr": pr.pr_number, "vendors": [v.code for v in vendors]})
    return rfq


@transaction.atomic
def issue_rfq(rfq_id, user) -> RFQ:
    rfq = RFQ.objects.select_for_update().get(pk=rfq_id)
    if rfq.status != RFQStatus.DRAFT:
        raise WorkflowError("Only a draft RFQ can be issued.")
    if rfq.response_deadline <= timezone.now():
        raise WorkflowError("The response deadline has passed. Edit it in the admin console before issuing.")
    rfq.status, rfq.issued_at = RFQStatus.ISSUED, timezone.now()
    rfq.save()
    record(user, "RFQ_ISSUED", rfq)
    return rfq


@transaction.atomic
def close_rfq(rfq_id, user=None) -> RFQ:
    rfq = RFQ.objects.select_for_update().get(pk=rfq_id)
    if rfq.status != RFQStatus.ISSUED:
        raise WorkflowError("Only an issued RFQ can be closed for responses.")
    rfq.status, rfq.closed_at = RFQStatus.RESPONSES_CLOSED, timezone.now()
    rfq.save()
    record(user, "RFQ_RESPONSES_CLOSED", rfq)
    return rfq


def close_expired_rfqs() -> int:
    """Called by the hourly cron: close every ISSUED RFQ whose deadline has passed."""
    ids = list(RFQ.objects.filter(status=RFQStatus.ISSUED, response_deadline__lte=timezone.now()).values_list("pk", flat=True))
    for pk in ids:
        close_rfq(pk, None)
    return len(ids)


@transaction.atomic
def cancel_rfq(rfq_id, user) -> RFQ:
    """Cancel an RFQ; the PR returns to APPROVED if no other live RFQ exists, so a new RFQ can be raised."""
    rfq = RFQ.objects.select_for_update().get(pk=rfq_id)
    if rfq.status == RFQStatus.CANCELLED:
        raise WorkflowError("This RFQ is already cancelled.")
    rfq.status = RFQStatus.CANCELLED
    rfq.save()
    pr = PurchaseRequisition.objects.select_for_update().get(pk=rfq.pr_id)
    if pr.status == PRStatus.RFQ_CREATED and not pr.rfqs.exclude(status=RFQStatus.CANCELLED).exists():
        pr.status = PRStatus.APPROVED
        pr.save(update_fields=["status", "updated_at"])
    record(user, "RFQ_CANCELLED", rfq)
    return rfq


# --------------------------------------------------------------------------- quotations
def record_quotation(rfq_id, vendor_id, user, header: dict, lines: list, upload=None) -> Quotation:
    """
    Store a vendor quotation: DB header + lines (Neon) and the vendor's document (Blob).

    header: quote_date, valid_until, vendor_ref_no, payment_terms, lead_time_days, notes
    lines:  [{"rfq_line_id", "unit_price", "quantity", "delivery_days", "remarks"}, ...]
    A repeat quote from the same vendor becomes a new revision; the previous one is SUPERSEDED.
    Blob upload happens first; if the DB part fails the orphaned blob is deleted.
    """
    stored = doc_services.upload_to_storage(upload, f"quotations/rfq-{rfq_id}") if upload else None
    try:
        with transaction.atomic():
            quote = _create_quotation(rfq_id, vendor_id, user, header, lines)
            if stored:
                doc_services.register_document(stored, "QUOTATION", quote.pk, "QUOTATION_PDF", user)
            return quote
    except Exception:
        if stored:
            doc_services.discard_stored(stored)
        raise


def _create_quotation(rfq_id, vendor_id, user, header, lines) -> Quotation:
    rfq = RFQ.objects.select_for_update().get(pk=rfq_id)
    if rfq.status not in (RFQStatus.ISSUED, RFQStatus.RESPONSES_CLOSED):
        raise WorkflowError("Quotations can only be recorded for an issued RFQ.")
    invite = rfq.rfq_vendors.filter(vendor_id=vendor_id).first()
    if invite is None:
        raise WorkflowError("This vendor was not invited to the RFQ.")

    valid_lines = {l.pk: l for l in rfq.lines.all()}
    priced = [l for l in lines if l.get("unit_price") is not None]
    if not priced:
        raise WorkflowError("Enter a price for at least one line.")
    if any(l["rfq_line_id"] not in valid_lines for l in priced):
        raise WorkflowError("A quotation line does not belong to this RFQ.")

    previous = rfq.quotations.filter(vendor_id=vendor_id).order_by("-revision_no").first()
    revision = (previous.revision_no + 1) if previous else 1
    if previous:
        rfq.quotations.filter(vendor_id=vendor_id).exclude(status=QuotationStatus.SUPERSEDED).update(status=QuotationStatus.SUPERSEDED)

    quote = Quotation.objects.create(
        quote_number=next_number("QTN"), rfq=rfq, vendor_id=vendor_id, revision_no=revision,
        is_late=timezone.now() > rfq.response_deadline, entered_by=user, **header,
    )
    total = Decimal("0")
    out = []
    for l in priced:
        line_total = (Decimal(l["quantity"]) * Decimal(l["unit_price"])).quantize(_CENT, rounding=ROUND_HALF_UP)
        total += line_total
        out.append(QuotationLine(quotation=quote, rfq_line_id=l["rfq_line_id"], unit_price=l["unit_price"],
                                 quantity=l["quantity"], line_total=line_total,
                                 delivery_days=l.get("delivery_days"), remarks=l.get("remarks", "")))
    QuotationLine.objects.bulk_create(out)
    quote.total_amount = total
    quote.save(update_fields=["total_amount", "updated_at"])

    invite.status = RFQVendor.Status.RESPONDED
    invite.save(update_fields=["status"])
    record(user, "QUOTATION_RECORDED", quote, data={"rfq": rfq.rfq_number, "vendor": invite.vendor.code,
                                                    "revision": revision, "total": total, "late": quote.is_late})
    return quote


@transaction.atomic
def set_quotation_status(quote_id, user, status) -> Quotation:
    if status not in (QuotationStatus.UNDER_REVIEW, QuotationStatus.SHORTLISTED, QuotationStatus.REJECTED):
        raise WorkflowError("Invalid status.")
    quote = Quotation.objects.select_for_update().get(pk=quote_id)
    if quote.status == QuotationStatus.SUPERSEDED:
        raise WorkflowError("A superseded revision cannot be changed.")
    old, quote.status = quote.status, status
    quote.save(update_fields=["status", "updated_at"])
    record(user, "QUOTATION_STATUS", quote, data={"from": old, "to": status})
    return quote
