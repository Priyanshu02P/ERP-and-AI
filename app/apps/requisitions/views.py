"""Thin HTTP layer for PRs: parse input, call policies/services, render."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import PR_CREATOR_ROLES, Role
from apps.accounts.permissions import role_required
from apps.core.exceptions import WorkflowError
from apps.documents.services import list_documents

from . import services
from .forms import DecisionForm, PRForm, PRLineFormSet
from .models import PRStatus, PurchaseRequisition, PRApprovalStep, StepStatus
from .policies import approval_inbox, can_edit_pr, can_view_pr, decision_blocker, visible_prs


@login_required
def pr_list(request):
    qs = visible_prs(request.user)
    status, q = request.GET.get("status", ""), request.GET.get("q", "").strip()
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(pr_number__icontains=q) | Q(title__icontains=q))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "requisitions/pr_list.html",
                  {"page_obj": page, "statuses": PRStatus.choices, "status": status, "q": q})


def _edit_view(request, pr, title):
    form = PRForm(request.POST or None, instance=pr, initial={"department": request.user.department})
    formset = PRLineFormSet(request.POST or None, instance=pr)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        pr = services.save_pr_with_lines(form, formset, request.user)
        messages.success(request, f"{pr.pr_number} saved.")
        return redirect("requisitions:pr_detail", pk=pr.pk)
    return render(request, "requisitions/pr_form.html", {"form": form, "formset": formset, "title": title})


@role_required(*PR_CREATOR_ROLES)
def pr_create(request):
    return _edit_view(request, PurchaseRequisition(), "New purchase requisition")


@login_required
def pr_edit(request, pk):
    pr = get_object_or_404(PurchaseRequisition, pk=pk)
    if not can_edit_pr(request.user, pr):
        raise PermissionDenied
    return _edit_view(request, pr, f"Edit {pr.pr_number}")


@login_required
def pr_detail(request, pk):
    pr = get_object_or_404(PurchaseRequisition.objects.select_related("department", "category", "requested_by", "doa_rule"), pk=pk)
    if not can_view_pr(request.user, pr):
        raise PermissionDenied
    steps = list(pr.approval_steps.select_related("decided_by"))
    cycles = {}
    for s in steps:
        cycles.setdefault(s.cycle_no, []).append(s)
    open_step = next((s for s in steps if s.status == StepStatus.PENDING and s.cycle_no == pr.current_cycle
                      and decision_blocker(request.user, _with_pr(s, pr)) is None), None)
    ctx = {
        "pr": pr, "lines": pr.lines.all(), "cycles": sorted(cycles.items(), reverse=True),
        "documents": list_documents("PR", pr.pk), "can_edit": can_edit_pr(request.user, pr),
        "can_cancel": pr.requested_by_id == request.user.pk and pr.status in (PRStatus.DRAFT, PRStatus.RETURNED, PRStatus.IN_APPROVAL),
        "can_create_rfq": request.user.can_source and pr.status == PRStatus.APPROVED,
        "open_step": open_step, "decision_form": DecisionForm(), "rfqs": pr.rfqs.all(),
    }
    return render(request, "requisitions/pr_detail.html", ctx)


def _with_pr(step, pr):
    step.pr = pr
    return step


@login_required
@require_POST
def pr_submit(request, pk):
    try:
        pr = services.submit_pr(pk, request.user)
        messages.success(request, f"{pr.pr_number} submitted for approval.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("requisitions:pr_detail", pk=pk)


@login_required
@require_POST
def pr_cancel(request, pk):
    try:
        pr = services.cancel_pr(pk, request.user)
        messages.success(request, f"{pr.pr_number} cancelled.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("requisitions:pr_detail", pk=pk)


@role_required(Role.MANAGER, Role.CPO)
def inbox(request):
    return render(request, "requisitions/inbox.html", {"steps": approval_inbox(request.user)})


@role_required(Role.MANAGER, Role.CPO)
@require_POST
def decide(request, step_id):
    step = get_object_or_404(PRApprovalStep, pk=step_id)
    decision = request.POST.get("decision", "")
    try:
        pr = services.decide(step_id, request.user, decision, request.POST.get("comment", ""))
        messages.success(request, f"{pr.pr_number}: {decision.title()} recorded.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("requisitions:pr_detail", pk=step.pr_id)
