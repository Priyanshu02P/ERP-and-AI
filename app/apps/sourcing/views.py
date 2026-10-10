"""Thin HTTP layer for RFQs and quotations."""
import requests
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.forms import formset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import role_required
from apps.accounts.roles import SOURCING_ROLES
from apps.core.exceptions import WorkflowError
from apps.documents.services import list_documents
from apps.requisitions.models import PRStatus, PurchaseRequisition
from apps.requisitions.policies import can_view_pr

from . import services
from .forms import QuotationHeaderForm, QuotationLineForm, RFQCreateForm
from .models import RFQ, Quotation, QuotationStatus, RFQStatus
from .policies import can_attach_to_rfq, can_manage_rfq, can_view_rfq, visible_rfqs

QuotationLineFormSet = formset_factory(QuotationLineForm, extra=0)


@login_required
def rfq_list(request):
    qs = visible_rfqs(request.user)
    status = request.GET.get("status", "")
    if status:
        qs = qs.filter(status=status)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "sourcing/rfq_list.html", {"page_obj": page, "statuses": RFQStatus.choices, "status": status})


@role_required(*SOURCING_ROLES)
def rfq_create(request, pr_id):
    pr = get_object_or_404(PurchaseRequisition, pk=pr_id)
    if not can_view_pr(request.user, pr):
        raise PermissionDenied
    if pr.status != PRStatus.APPROVED:
        messages.error(request, "An RFQ can only be created from an approved PR.")
        return redirect("requisitions:pr_detail", pk=pr.pk)
    form = RFQCreateForm(request.POST or None, initial={"title": pr.title})
    if request.method == "POST" and form.is_valid():
        try:
            rfq = services.create_rfq_from_pr(pr.pk, request.user, list(form.cleaned_data["vendors"]),
                                              form.cleaned_data["response_deadline"], form.cleaned_data["title"],
                                              form.cleaned_data["terms"])
            messages.success(request, f"{rfq.rfq_number} created as draft. Review it, then issue it.")
            return redirect("sourcing:rfq_detail", pk=rfq.pk)
        except WorkflowError as exc:
            messages.error(request, str(exc))
    return render(request, "sourcing/rfq_create.html", {"form": form, "pr": pr, "lines": pr.lines.all()})


@login_required
def rfq_detail(request, pk):
    rfq = get_object_or_404(RFQ.objects.select_related("pr", "created_by"), pk=pk)
    if not can_view_rfq(request.user, rfq):
        raise PermissionDenied
    quotes = list(rfq.quotations.select_related("vendor", "entered_by"))
    ctx = {
        "rfq": rfq, "lines": rfq.lines.all(), "rfq_vendors": rfq.rfq_vendors.select_related("vendor"),
        "quotes": quotes, "documents": list_documents("RFQ", rfq.pk),
        "can_manage": can_manage_rfq(request.user), "can_attach": can_attach_to_rfq(request.user, rfq),
        "can_record": can_manage_rfq(request.user) and rfq.status in (RFQStatus.ISSUED, RFQStatus.RESPONSES_CLOSED),
    }
    return render(request, "sourcing/rfq_detail.html", ctx)


def _rfq_action(request, pk, action, ok_message):
    rfq = get_object_or_404(RFQ, pk=pk)
    if not (can_view_rfq(request.user, rfq) and can_manage_rfq(request.user)):
        raise PermissionDenied
    try:
        action(pk, request.user)
        messages.success(request, ok_message)
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("sourcing:rfq_detail", pk=pk)


@login_required
@require_POST
def rfq_issue(request, pk):
    return _rfq_action(request, pk, services.issue_rfq, "RFQ issued. Vendors can now be quoted against it.")


@login_required
@require_POST
def rfq_close(request, pk):
    return _rfq_action(request, pk, services.close_rfq, "RFQ closed for responses.")


@login_required
@require_POST
def rfq_cancel(request, pk):
    return _rfq_action(request, pk, services.cancel_rfq, "RFQ cancelled.")


@role_required(*SOURCING_ROLES)
def quotation_create(request, rfq_id):
    rfq = get_object_or_404(RFQ, pk=rfq_id)
    if not can_view_rfq(request.user, rfq):
        raise PermissionDenied
    if rfq.status not in (RFQStatus.ISSUED, RFQStatus.RESPONSES_CLOSED):
        messages.error(request, "Quotations can only be recorded for an issued RFQ.")
        return redirect("sourcing:rfq_detail", pk=rfq.pk)

    rfq_lines = list(rfq.lines.all())
    initial = [{"rfq_line_id": l.pk, "quantity": l.quantity} for l in rfq_lines]
    descriptions = {l.pk: f"{l.line_no}. {l.description} ({l.quantity.normalize():f} {l.uom})" for l in rfq_lines}
    header = QuotationHeaderForm(request.POST or None, request.FILES or None, rfq=rfq,
                                 initial={"vendor": request.GET.get("vendor")})
    formset = QuotationLineFormSet(request.POST or None, initial=initial)
    if request.method == "POST" and header.is_valid() and formset.is_valid():
        h = header.cleaned_data
        lines = [{k: f.cleaned_data.get(k) for k in ("rfq_line_id", "unit_price", "quantity", "delivery_days", "remarks")}
                 for f in formset]
        try:
            quote = services.record_quotation(
                rfq.pk, h["vendor"].pk, request.user,
                {k: h[k] for k in ("vendor_ref_no", "quote_date", "valid_until", "payment_terms", "lead_time_days", "notes")},
                lines, upload=h.get("file"))
            messages.success(request, f"{quote.quote_number} recorded" + (" (marked late)." if quote.is_late else "."))
            return redirect("sourcing:quotation_detail", pk=quote.pk)
        except WorkflowError as exc:
            messages.error(request, str(exc))
        except requests.RequestException:
            messages.error(request, "File storage is unavailable; nothing was saved. Try again shortly.")
    for form in formset:  # attach descriptions for display
        form.line_label = descriptions.get(form["rfq_line_id"].value() and int(form["rfq_line_id"].value()), "")
    return render(request, "sourcing/quotation_form.html", {"rfq": rfq, "header": header, "formset": formset})


@login_required
def quotation_detail(request, pk):
    quote = get_object_or_404(Quotation.objects.select_related("rfq", "vendor", "entered_by"), pk=pk)
    if not can_view_rfq(request.user, quote.rfq):
        raise PermissionDenied
    ctx = {
        "quote": quote, "lines": quote.lines.select_related("rfq_line"),
        "documents": list_documents("QUOTATION", quote.pk), "can_manage": can_manage_rfq(request.user),
        "statuses": [QuotationStatus.UNDER_REVIEW, QuotationStatus.SHORTLISTED, QuotationStatus.REJECTED],
    }
    return render(request, "sourcing/quotation_detail.html", ctx)


@role_required(*SOURCING_ROLES)
@require_POST
def quotation_status(request, pk):
    quote = get_object_or_404(Quotation, pk=pk)
    if not can_view_rfq(request.user, quote.rfq):
        raise PermissionDenied
    try:
        services.set_quotation_status(pk, request.user, request.POST.get("status", ""))
        messages.success(request, "Quotation status updated.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("sourcing:quotation_detail", pk=pk)
