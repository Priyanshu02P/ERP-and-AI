"""Thin HTTP layer for purchase orders."""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import role_required
from apps.accounts.roles import SOURCING_ROLES, Role
from apps.core.exceptions import WorkflowError
from apps.documents.services import list_documents
from apps.requisitions.models import PRStatus, PurchaseRequisition
from apps.requisitions.policies import can_view_pr

from . import services
from .forms import DecisionForm, POForm, line_formset
from .models import (EDITABLE_STATUSES, LIVE_STATUSES, POApprovalStep, POStatus, PurchaseOrder, PurchasingPolicy,
                     SourcingBasis)
from .policies import approval_inbox, can_edit_po, can_operate_po, can_submit_po, can_view_po, decision_blocker, visible_pos
from .schedule import upcoming


def _po_or_403(request, pk):
    po = get_object_or_404(PurchaseOrder.objects.select_related(
        "vendor", "department", "category", "created_by", "pr", "evaluation__rfq", "renewal_of", "parent", "doa_rule"), pk=pk)
    if not can_view_po(request.user, po):
        raise PermissionDenied
    return po


@login_required
def po_list(request):
    qs = visible_pos(request.user)
    status, basis, q = request.GET.get("status", ""), request.GET.get("basis", ""), request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    if status:
        qs = qs.filter(status=status)
    if basis:
        qs = qs.filter(basis=basis)
    if kind == "recurring":
        qs = qs.filter(is_recurring=True)
    elif kind == "releases":
        qs = qs.filter(parent__isnull=False)
    elif not (status or basis or q):
        qs = qs.filter(parent__isnull=True)  # releases are shown inside their agreement unless asked for
    if q:
        qs = qs.filter(Q(po_number__icontains=q) | Q(title__icontains=q) | Q(vendor__name__icontains=q))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "purchasing/po_list.html", {
        "page_obj": page, "statuses": POStatus.choices, "bases": SourcingBasis.choices,
        "status": status, "basis": basis, "q": q, "kind": kind, "policy": PurchasingPolicy.get()})


def _edit(request, po, title, initial=None, line_initial=None):
    Formset = line_formset(extra=max(3, len(line_initial or [])))
    form = POForm(request.POST or None, instance=po, initial=initial)
    formset = Formset(request.POST or None, instance=po, initial=None if request.POST else line_initial)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        po = services.save_po_with_lines(form, formset, request.user)
        messages.success(request, f"{po.po_number} saved.")
        return redirect("purchasing:po_detail", pk=po.pk)
    return render(request, "purchasing/po_form.html", {"form": form, "formset": formset, "title": title,
                                                      "policy": PurchasingPolicy.get(), "po": po})


@role_required(*SOURCING_ROLES)
def po_create(request):
    """Direct PO without an RFQ. Optionally pre-filled from an approved PR via ?pr=<id>."""
    initial, line_initial = {"department": request.user.department_id}, []
    pr_id = request.GET.get("pr")
    if pr_id and request.method == "GET":
        pr = get_object_or_404(PurchaseRequisition, pk=pr_id)
        if not can_view_pr(request.user, pr) or pr.status != PRStatus.APPROVED:
            raise PermissionDenied
        initial.update(title=pr.title, department=pr.department_id, category=pr.category_id, pr=pr.pk,
                       justification=pr.justification)
        line_initial = [{"description": l.description, "quantity": l.quantity, "uom": l.uom,
                         "unit_price": l.est_unit_price} for l in pr.lines.all()]
    return _edit(request, PurchaseOrder(), "New purchase order (no RFQ)", initial, line_initial)


@login_required
def po_edit(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if not can_edit_po(request.user, po):
        raise PermissionDenied
    return _edit(request, po, f"Edit {po.po_number}")


@login_required
def po_detail(request, pk):
    po = _po_or_403(request, pk)
    steps = list(po.approval_steps.select_related("decided_by"))
    cycles = {}
    for s in steps:
        cycles.setdefault(s.cycle_no, []).append(s)
    for s in steps:
        s.po = po
    open_step = next((s for s in steps if s.status == POApprovalStep.Status.PENDING and s.cycle_no == po.current_cycle
                      and decision_blocker(request.user, s) is None), None)
    policy = PurchasingPolicy.get()
    preview = services.determine_sourcing(po) if po.status in EDITABLE_STATUSES and po.pk else None
    releases = list(po.releases.order_by("scheduled_for")) if po.is_recurring else []
    ctx = {
        "po": po, "lines": po.lines.all(), "cycles": sorted(cycles.items(), reverse=True), "open_step": open_step,
        "preview_label": SourcingBasis(preview.basis).label if preview else "",
        "decision_form": DecisionForm(), "documents": list_documents("PO", po.pk), "policy": policy, "preview": preview,
        "releases": releases, "renewal": po.renewals.exclude(status__in=[POStatus.CANCELLED, POStatus.REJECTED]).first(),
        "upcoming": upcoming(po.start_date, po.end_date, po.frequency, po.schedule_index) if po.is_recurring and po.status in (POStatus.ACTIVE, POStatus.PAUSED, POStatus.APPROVED) else [],
        "can_edit": can_edit_po(request.user, po), "can_submit": can_submit_po(request.user, po),
        "can_cancel_early": po.created_by_id == request.user.pk and po.status in EDITABLE_STATUSES + (POStatus.IN_APPROVAL,),
        "can_cancel_late": request.user.role in (Role.MANAGER, Role.CPO) and po.status in (POStatus.APPROVED,) + LIVE_STATUSES,
        "can_operate": can_operate_po(request.user),
    }
    return render(request, "purchasing/po_detail.html", ctx)


@login_required
def po_print(request, pk):
    po = _po_or_403(request, pk)
    return render(request, "purchasing/po_print.html", {"po": po, "lines": po.lines.all()})


def _action(request, pk, fn, ok, **kwargs):
    po = _po_or_403(request, pk)
    try:
        fn(po.pk, request.user, **kwargs)
        messages.success(request, ok)
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("purchasing:po_detail", pk=pk)


@login_required
@require_POST
def po_submit(request, pk):
    return _action(request, pk, services.submit_po, "Submitted for approval.")


@login_required
@require_POST
def po_cancel(request, pk):
    return _action(request, pk, services.cancel_po, "PO cancelled.", reason=request.POST.get("reason", ""))


@role_required(*SOURCING_ROLES)
@require_POST
def po_issue(request, pk):
    return _action(request, pk, services.issue_po, "PO issued.")


@role_required(*SOURCING_ROLES)
@require_POST
def po_close(request, pk):
    return _action(request, pk, services.close_po, "PO closed.")


@role_required(*SOURCING_ROLES)
@require_POST
def po_pause(request, pk):
    return _action(request, pk, services.pause_po, "Agreement paused. No releases are generated while paused.")


@role_required(*SOURCING_ROLES)
@require_POST
def po_resume(request, pk):
    return _action(request, pk, services.resume_po, "Agreement resumed. Occurrences missed while paused were skipped.")


@role_required(*SOURCING_ROLES)
@require_POST
def po_from_evaluation(request, evaluation_id):
    from apps.evaluation.models import Evaluation
    from apps.sourcing.policies import can_view_rfq
    ev = get_object_or_404(Evaluation.objects.select_related("rfq__pr"), pk=evaluation_id)
    if not can_view_rfq(request.user, ev.rfq):
        raise PermissionDenied
    existing = PurchaseOrder.objects.filter(evaluation=ev).exclude(status__in=[POStatus.CANCELLED, POStatus.REJECTED]).first()
    if existing:
        messages.info(request, f"{existing.po_number} already exists for this evaluation.")
        return redirect("purchasing:po_detail", pk=existing.pk)
    try:
        po = services.create_po_from_evaluation(ev.pk, request.user)
        messages.success(request, f"{po.po_number} drafted from the evaluation. Submit it for approval (award approval).")
        return redirect("purchasing:po_detail", pk=po.pk)
    except WorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("evaluation:detail", rfq_id=ev.rfq_id)


@role_required(*SOURCING_ROLES)
@require_POST
def po_renew(request, pk):
    po = _po_or_403(request, pk)
    try:
        new = services.renew_po(po.pk, request.user)
        messages.success(request, f"Renewal {new.po_number} drafted. Review dates and prices, then submit.")
        return redirect("purchasing:po_edit", pk=new.pk)
    except WorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("purchasing:po_detail", pk=pk)


@login_required
def renewals(request):
    """Agreements/POs ending soon (or recently ended) that have no renewal yet. Replaces reminders/notifications."""
    policy, today = PurchasingPolicy.get(), timezone.localdate()
    qs = (visible_pos(request.user).filter(parent__isnull=True, end_date__isnull=False, status__in=LIVE_STATUSES + (POStatus.CLOSED,),
                                           end_date__lte=today + timedelta(days=policy.renewal_lookahead_days),
                                           end_date__gte=today - timedelta(days=90))
          .exclude(renewals__status__in=[s for s in POStatus.values if s not in (POStatus.CANCELLED, POStatus.REJECTED)])
          .order_by("end_date"))
    rows = [{"po": po, "days_left": (po.end_date - today).days} for po in qs]
    return render(request, "purchasing/renewals.html", {"rows": rows, "policy": policy})


@role_required(Role.MANAGER, Role.CPO)
def inbox(request):
    return render(request, "purchasing/inbox.html", {"steps": approval_inbox(request.user)})


@role_required(Role.MANAGER, Role.CPO)
@require_POST
def decide(request, step_id):
    step = get_object_or_404(POApprovalStep, pk=step_id)
    decision = request.POST.get("decision", "")
    try:
        po = services.decide(step_id, request.user, decision, request.POST.get("comment", ""))
        messages.success(request, f"{po.po_number}: {decision.title()} recorded.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("purchasing:po_detail", pk=step.po_id)
