"""Thin HTTP layer: bid comparison, evaluation editing, review."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import role_required
from apps.accounts.roles import SOURCING_ROLES
from apps.core.exceptions import WorkflowError
from apps.documents.services import list_documents
from apps.sourcing.models import RFQ, RFQStatus
from apps.sourcing.policies import can_manage_rfq, can_view_rfq

from . import services
from .comparison import build_comparison
from .forms import EntryForm, MetaForm, ReviewForm, WeightsForm
from .models import MANUAL_CRITERIA, Criterion, Evaluation, EvaluationStatus
from .policies import review_blocker
from .scoring import compute_results, results_to_dicts


def _rfq_or_403(request, rfq_id):
    rfq = get_object_or_404(RFQ.objects.select_related("pr"), pk=rfq_id)
    if not can_view_rfq(request.user, rfq):
        raise PermissionDenied
    return rfq


def _criteria(weights):
    return [{"code": c, "label": label, "weight": weights.get(c, 0)} for c, label in Criterion.choices]


@login_required
def compare(request, rfq_id):
    rfq = _rfq_or_403(request, rfq_id)
    evaluation = Evaluation.objects.filter(rfq=rfq).first()
    data = build_comparison(rfq)
    return render(request, "evaluation/compare.html", {
        "rfq": rfq, "cmp": data, "evaluation": evaluation, "today": timezone.localdate(),
        "can_start": can_manage_rfq(request.user) and evaluation is None and bool(data.quotes)
                     and rfq.status == RFQStatus.RESPONSES_CLOSED,
        "needs_close": rfq.status == RFQStatus.ISSUED,
    })


@role_required(*SOURCING_ROLES)
@require_POST
def start(request, rfq_id):
    rfq = _rfq_or_403(request, rfq_id)
    try:
        services.start_evaluation(rfq.pk, request.user)
        messages.success(request, "Evaluation started. Confirm the gates, then score each bidder.")
        return redirect("evaluation:edit", rfq_id=rfq.pk)
    except WorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("evaluation:compare", rfq_id=rfq.pk)


def _matrix_rows(entry_forms):
    """Rows of bound fields (one cell per bidder) for the scoring grid."""
    def row(label, names, hint=""):
        return {"label": label, "hint": hint, "cells": [[f[n] for n in names] for _, f in entry_forms]}

    rows = [
        row("Gate: meets mandatory specifications", ["gate_specs"]),
        row("Gate: required certificates available", ["gate_certificates"]),
        row("Gate: capacity confirmed", ["gate_capacity"]),
        row("Disqualify bidder", ["is_disqualified", "disqualification_reason"]),
        row("Cost adjustment (added to landed cost)", ["adjustment_amount", "adjustment_note"],
            "Inspection, expected defect cost, financing, downtime. Do not double-count costs already quoted."),
    ]
    for code, label in Criterion.choices:
        if code != Criterion.COST:
            rows.append(row(f"Score 0-100: {label}", [f"score_{code}", f"note_{code}"]))
    return rows


@role_required(*SOURCING_ROLES)
def edit(request, rfq_id):
    rfq = _rfq_or_403(request, rfq_id)
    ev = get_object_or_404(Evaluation, rfq=rfq)
    if ev.status != EvaluationStatus.DRAFT:
        messages.info(request, "This evaluation is locked; showing the read-only view.")
        return redirect("evaluation:detail", rfq_id=rfq.pk)

    entries = list(ev.entries.select_related("quotation__vendor").prefetch_related("scores"))
    post = request.POST if request.method == "POST" else None
    action = request.POST.get("action", "save")

    if post and action == "refresh":
        try:
            services.refresh_entries(ev.pk, request.user)
            messages.success(request, "Bidders refreshed from the latest quotations.")
        except WorkflowError as exc:
            messages.error(request, str(exc))
        return redirect("evaluation:edit", rfq_id=rfq.pk)

    weights_form = WeightsForm(post, initial={f"w_{k}": v for k, v in ev.weights.items()})
    entry_forms = [(e, EntryForm(post, prefix=f"e{e.pk}", initial=EntryForm.initial_for(e))) for e in entries]
    meta_form = MetaForm(post, evaluation=ev, initial={"recommended": ev.recommended_entry_id, "rationale": ev.rationale})

    if post:
        forms_ok = [weights_form.is_valid(), meta_form.is_valid()] + [f.is_valid() for _, f in entry_forms]
        if all(forms_ok):
            try:
                rec = meta_form.cleaned_data["recommended"]
                services.save_evaluation(
                    ev.pk, request.user, weights=weights_form.weights(),
                    entries={e.pk: f.service_data() for e, f in entry_forms},
                    recommended_entry_id=rec.pk if rec else None, rationale=meta_form.cleaned_data["rationale"])
                if action == "submit":
                    services.submit_evaluation(ev.pk, request.user)
                    messages.success(request, "Evaluation submitted for review.")
                    return redirect("evaluation:detail", rfq_id=rfq.pk)
                messages.success(request, "Evaluation saved.")
                return redirect("evaluation:edit", rfq_id=rfq.pk)
            except WorkflowError as exc:
                messages.error(request, str(exc))
                if action == "submit":  # the save part succeeded; show the saved state
                    return redirect("evaluation:edit", rfq_id=rfq.pk)

    ev.refresh_from_db()
    return render(request, "evaluation/edit.html", {
        "rfq": rfq, "ev": ev, "weights_form": weights_form, "meta_form": meta_form,
        "entry_forms": entry_forms, "rows": _matrix_rows(entry_forms),
        "results": results_to_dicts(compute_results(ev)), "criteria": _criteria(ev.weights),
        "documents": list_documents("EVALUATION", ev.pk), "can_attach": True,
        "stale": rfq.quotations.exclude(status__in=["SUPERSEDED", "REJECTED"]).count() != len(entries),
    })


@login_required
def detail(request, rfq_id):
    rfq = _rfq_or_403(request, rfq_id)
    ev = get_object_or_404(Evaluation.objects.select_related("recommended_entry__quotation__vendor", "submitted_by", "reviewed_by"), rfq=rfq)
    if ev.status == EvaluationStatus.FINALIZED and ev.result_snapshot:
        results, weights = ev.result_snapshot["results"], ev.result_snapshot["weights"]
    else:
        results, weights = results_to_dicts(compute_results(ev)), ev.weights
    top = next((r for r in results if r["rank"] == 1), None)
    rec = ev.recommended_entry
    return render(request, "evaluation/detail.html", {
        "rfq": rfq, "ev": ev, "results": results, "criteria": _criteria(weights), "top": top,
        "not_top": bool(rec and top and top["entry_id"] != rec.pk),
        "blocker": review_blocker(request.user, ev), "review_form": ReviewForm(),
        "can_edit": can_manage_rfq(request.user) and ev.status == EvaluationStatus.DRAFT,
        "documents": list_documents("EVALUATION", ev.pk),
    })


@login_required
@require_POST
def review(request, rfq_id):
    rfq = _rfq_or_403(request, rfq_id)
    ev = get_object_or_404(Evaluation, rfq=rfq)
    try:
        services.review_evaluation(ev.pk, request.user, request.POST.get("decision", ""), request.POST.get("comment", ""))
        messages.success(request, "Review recorded.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("evaluation:detail", rfq_id=rfq.pk)
