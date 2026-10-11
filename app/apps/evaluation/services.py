"""
Evaluation use-cases. Every state change is transactional and audited.

    start_evaluation -> (edit: save_evaluation / refresh_entries) -> submit_evaluation
        -> review_evaluation(FINALIZE | RETURN)
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.core.exceptions import WorkflowError
from apps.sourcing.models import RFQ, Quotation, QuotationStatus, RFQStatus

from .models import (MANUAL_CRITERIA, Criterion, Evaluation, EvaluationEntry, EvaluationScore, EvaluationStatus)
from .policies import review_blocker
from .scoring import compute_results, results_to_dicts

FINALIZE, RETURN = "FINALIZE", "RETURN"


def _lock(evaluation_id) -> Evaluation:
    return Evaluation.objects.select_for_update().select_related("rfq", "rfq__pr").get(pk=evaluation_id)


def _require_draft(ev):
    if ev.status != EvaluationStatus.DRAFT:
        raise WorkflowError("This evaluation is locked. It can only be edited while in draft.")


def validate_weights(weights: dict) -> dict:
    try:
        clean = {c: int(weights[c]) for c in Criterion.values}
    except (KeyError, TypeError, ValueError):
        raise WorkflowError("Provide a whole-number weight for every criterion.")
    if any(w < 0 or w > 100 for w in clean.values()):
        raise WorkflowError("Each weight must be between 0 and 100.")
    if sum(clean.values()) != 100:
        raise WorkflowError(f"Weights must total 100% (currently {sum(clean.values())}%).")
    return clean


def sync_entries(ev: Evaluation) -> None:
    """Make the entries match the RFQ's current quotations (adds new ones, drops superseded/rejected)."""
    current = list(Quotation.objects.filter(rfq=ev.rfq).exclude(status__in=[QuotationStatus.SUPERSEDED, QuotationStatus.REJECTED]))
    ev.entries.exclude(quotation_id__in=[q.pk for q in current]).delete()
    have = set(ev.entries.values_list("quotation_id", flat=True))
    for q in current:
        if q.pk not in have:  # vendor claims pre-fill the gates; the evaluator still has to confirm them
            EvaluationEntry.objects.create(
                evaluation=ev, quotation=q, gate_specs=q.meets_specs,
                gate_certificates=q.certificates_available, gate_capacity=q.capacity_confirmed)


@transaction.atomic
def start_evaluation(rfq_id, user) -> Evaluation:
    rfq = RFQ.objects.select_for_update().get(pk=rfq_id)
    if rfq.status != RFQStatus.RESPONSES_CLOSED:
        raise WorkflowError("Close the RFQ for responses before evaluating bids, so every bidder is judged on the same set.")
    if Evaluation.objects.filter(rfq=rfq).exists():
        raise WorkflowError("An evaluation already exists for this RFQ.")
    ev = Evaluation.objects.create(rfq=rfq, created_by=user)
    sync_entries(ev)
    if not ev.entries.exists():
        raise WorkflowError("There are no current quotations to evaluate.")
    record(user, "EVALUATION_STARTED", ev, data={"bidders": ev.entries.count()})
    return ev


@transaction.atomic
def refresh_entries(evaluation_id, user) -> Evaluation:
    ev = _lock(evaluation_id)
    _require_draft(ev)
    sync_entries(ev)
    if ev.recommended_entry_id and not ev.entries.filter(pk=ev.recommended_entry_id).exists():
        ev.recommended_entry = None
        ev.save(update_fields=["recommended_entry", "updated_at"])
    record(user, "EVALUATION_REFRESHED", ev)
    return ev


@transaction.atomic
def save_evaluation(evaluation_id, user, *, weights, entries, recommended_entry_id=None, rationale="") -> Evaluation:
    """
    weights: {criterion: int}
    entries: {entry_id: {gate_specs, gate_certificates, gate_capacity, is_disqualified, disqualification_reason,
                         adjustment_amount, adjustment_note, scores: {criterion: (score|None, note)}}}
    """
    ev = _lock(evaluation_id)
    _require_draft(ev)
    ev.weights = validate_weights(weights)
    by_id = {e.pk: e for e in ev.entries.all()}
    for entry_id, data in entries.items():
        entry = by_id.get(entry_id)
        if entry is None:
            raise WorkflowError("A bidder in the form is no longer part of this evaluation. Refresh and try again.")
        for attr in ("gate_specs", "gate_certificates", "gate_capacity", "is_disqualified", "disqualification_reason", "adjustment_note"):
            setattr(entry, attr, data[attr])
        entry.adjustment_amount = data.get("adjustment_amount") or Decimal("0")
        entry.save()
        for code in MANUAL_CRITERIA:
            score, note = data.get("scores", {}).get(code, (None, ""))
            if score is None:
                entry.scores.filter(criterion=code).delete()
            else:
                EvaluationScore.objects.update_or_create(entry=entry, criterion=code, defaults={"score": score, "note": note})
    ev.recommended_entry = by_id.get(recommended_entry_id)
    ev.rationale = (rationale or "").strip()
    ev.save()
    record(user, "EVALUATION_SAVED", ev)
    return ev


@transaction.atomic
def submit_evaluation(evaluation_id, user) -> Evaluation:
    """DRAFT -> UNDER_REVIEW after completeness checks."""
    ev = _lock(evaluation_id)
    _require_draft(ev)
    validate_weights(ev.weights)
    results = compute_results(ev)

    pending = [r.vendor for r in results if r.pending_gates]
    if pending:
        raise WorkflowError("Assess every mandatory gate (pass or fail) for: " + ", ".join(pending) + ".")
    eligible = [r for r in results if r.eligible]
    if not eligible:
        raise WorkflowError("No bidder passes the mandatory gates, so there is nothing to recommend. Consider re-tendering.")
    incomplete = [r.vendor for r in eligible if r.missing]
    if incomplete:
        raise WorkflowError("Score every criterion for eligible bidders: " + ", ".join(incomplete) + ".")
    if not ev.rationale:
        raise WorkflowError("Record the rationale for the recommendation.")

    top = next(r for r in sorted(eligible, key=lambda r: r.rank))
    if ev.recommended_entry_id is None:
        ev.recommended_entry_id = top.entry_id
    elif ev.recommended_entry_id not in {r.entry_id for r in eligible}:
        raise WorkflowError("The recommended bidder is not eligible.")

    ev.status, ev.submitted_by, ev.submitted_at = EvaluationStatus.UNDER_REVIEW, user, timezone.now()
    ev.save()
    record(user, "EVALUATION_SUBMITTED", ev, data={"recommended_entry": ev.recommended_entry_id,
                                                   "top_ranked": top.entry_id})
    return ev


@transaction.atomic
def review_evaluation(evaluation_id, user, decision, comment="") -> Evaluation:
    """Reviewer (not the submitter) finalises the evaluation or returns it for rework."""
    ev = _lock(evaluation_id)
    blocker = review_blocker(user, ev)
    if blocker:
        raise WorkflowError(blocker)
    comment = (comment or "").strip()
    if decision not in (FINALIZE, RETURN):
        raise WorkflowError("Unknown decision.")
    if decision == RETURN and not comment:
        raise WorkflowError("A comment is required when returning an evaluation.")

    ev.reviewed_by, ev.reviewed_at, ev.review_comment = user, timezone.now(), comment
    if decision == FINALIZE:
        ev.result_snapshot = {
            "weights": ev.weights, "frozen_at": timezone.now().isoformat(),
            "results": results_to_dicts(compute_results(ev)),
        }
        ev.status = EvaluationStatus.FINALIZED
    else:
        ev.status = EvaluationStatus.DRAFT
    ev.save()
    record(user, f"EVALUATION_{decision}D" if decision == FINALIZE else "EVALUATION_RETURNED", ev, data={"comment": comment})
    return ev
