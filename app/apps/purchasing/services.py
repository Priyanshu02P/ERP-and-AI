"""
PO business logic. Views call ONLY these functions to change state; every transition is
transactional, row-locked and audited.

    create (direct form | from_evaluation | renew_po) -> submit_po -> decide ... -> issue_po
        recurring master: ACTIVE -> generate_due_releases (cron) -> CLOSED
"""
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.roles import Role
from apps.audit.services import record
from apps.core.exceptions import WorkflowError
from apps.core.numbering import next_number
from apps.doa.resolver import DoAResolver
from apps.evaluation.models import Evaluation, EvaluationStatus
from apps.requisitions.models import PRStatus

from .models import (DIRECT_BASES, EDITABLE_STATUSES, LIVE_STATUSES, POApprovalStep, POLine, POStatus, PurchaseOrder,
                     PurchasingPolicy, SourcingBasis)
from .policies import decision_blocker
from .schedule import occurrence_date

DECISIONS = (POApprovalStep.Status.APPROVED, POApprovalStep.Status.REJECTED, POApprovalStep.Status.RETURNED)
MAX_CATCHUP_PER_RUN = 12   # never generate more than a year of back-dated monthly releases in one run


def _lock(po_id) -> PurchaseOrder:
    return PurchaseOrder.objects.select_for_update().select_related("vendor", "pr", "renewal_of").get(pk=po_id)


def recalculate_totals(po) -> None:
    lines = po.lines.aggregate(s=Sum("line_total"))["s"] or Decimal("0")
    po.total_amount = lines + po.charges_amount
    po.save(update_fields=["total_amount", "updated_at"])


def _renumber(po):
    for i, line in enumerate(po.lines.order_by("id"), start=1):
        if line.line_no != i:
            line.line_no = i
            line.save(update_fields=["line_no"])


# --------------------------------------------------------------------------- creation
@transaction.atomic
def save_po_with_lines(form, formset, user) -> PurchaseOrder:
    """Persist a validated direct/renewal PO form + lines (create or edit)."""
    po = form.save(commit=False)
    creating = po.pk is None
    if creating:
        po.created_by, po.po_number = user, next_number("PO")
    po.save()
    formset.save()
    _renumber(po)
    recalculate_totals(po)
    record(user, "PO_CREATED" if creating else "PO_UPDATED", po, data={"total": po.total_amount})
    return po


@transaction.atomic
def create_po_from_evaluation(evaluation_id, user) -> PurchaseOrder:
    """Competitive award: PO for the recommended bidder of a finalised evaluation (prices copied from the quote)."""
    ev = Evaluation.objects.select_for_update().select_related("rfq__pr", "recommended_entry__quotation__vendor").get(pk=evaluation_id)
    if ev.status != EvaluationStatus.FINALIZED or ev.recommended_entry_id is None:
        raise WorkflowError("A purchase order can only be raised from a finalised evaluation with a recommended bidder.")
    if PurchaseOrder.objects.filter(evaluation=ev).exclude(status__in=[POStatus.CANCELLED, POStatus.REJECTED]).exists():
        raise WorkflowError("A purchase order already exists for this evaluation.")
    quote, rfq = ev.recommended_entry.quotation, ev.rfq
    if not quote.vendor.is_active:
        raise WorkflowError("The recommended vendor is blocked. Resolve this before ordering.")

    charges = quote.landed_cost - quote.total_amount  # freight + taxes + setup - discount
    po = PurchaseOrder.objects.create(
        po_number=next_number("PO"), title=f"{rfq.title} - {quote.vendor.name}", vendor=quote.vendor,
        department=rfq.pr.department, category=rfq.pr.category, pr=rfq.pr, evaluation=ev, quotation=quote,
        basis=SourcingBasis.RFQ_AWARD, payment_terms=quote.payment_terms, charges_amount=charges, created_by=user,
        delivery_date=(timezone.localdate() + timedelta(days=quote.lead_time_days)) if quote.lead_time_days else None,
        notes=f"Awarded from {rfq.rfq_number}, quotation {quote.quote_number} (rev {quote.revision_no}).")
    POLine.objects.bulk_create(
        POLine(po=po, line_no=ql.rfq_line.line_no, description=ql.rfq_line.description, quantity=ql.quantity,
               uom=ql.rfq_line.uom, unit_price=ql.unit_price, line_total=ql.line_total)
        for ql in quote.lines.select_related("rfq_line"))
    recalculate_totals(po)
    record(user, "PO_CREATED_FROM_EVALUATION", po, data={"evaluation": ev.pk, "quotation": quote.quote_number, "total": po.total_amount})
    return po


@transaction.atomic
def renew_po(po_id, user, start_date=None) -> PurchaseOrder:
    """Draft renewal of an earlier PO/agreement: same vendor, lines and term length, new dates. Prices stay editable."""
    old = _lock(po_id)
    if old.parent_id:
        raise WorkflowError("Renew the master agreement, not an individual release.")
    if old.status not in LIVE_STATUSES + (POStatus.CLOSED,):
        raise WorkflowError("Only an issued, active or closed PO can be renewed.")
    if old.renewals.exclude(status__in=[POStatus.CANCELLED, POStatus.REJECTED]).exists():
        raise WorkflowError("This PO already has a renewal.")
    new_start = start_date or ((old.end_date + timedelta(days=1)) if old.end_date else timezone.localdate())
    new_end = None
    if old.start_date and old.end_date:
        new_end = new_start + (old.end_date - old.start_date)
    po = PurchaseOrder.objects.create(
        po_number=next_number("PO"), title=f"{old.title} (renewal)", vendor=old.vendor, department=old.department,
        category=old.category, renewal_of=old, basis=SourcingBasis.RENEWAL, payment_terms=old.payment_terms,
        charges_amount=old.charges_amount, is_recurring=old.is_recurring, frequency=old.frequency,
        start_date=new_start, end_date=new_end, delivery_date=None, created_by=user,
        notes=f"Renewal of {old.po_number}.")
    POLine.objects.bulk_create(POLine(po=po, line_no=l.line_no, description=l.description, quantity=l.quantity,
                                      uom=l.uom, unit_price=l.unit_price, line_total=l.line_total) for l in old.lines.all())
    recalculate_totals(po)
    record(user, "PO_RENEWAL_CREATED", po, data={"renews": old.po_number})
    return po


# --------------------------------------------------------------------------- sourcing rule
@dataclass
class Sourcing:
    basis: str
    needs_exemption: bool = False
    notes: list = field(default_factory=list)
    aggregate: Decimal = Decimal("0")
    threshold: Decimal = Decimal("0")
    uplift_pct: Decimal = None


def determine_sourcing(po) -> Sourcing:
    """
    Decide how this PO is sourced and whether it needs an exemption approval.
      award    -> competitive, no exemption
      renewal  -> RFQ-exempt unless whole-term value rises beyond the tolerance
      direct   -> allowed w/o RFQ if (this + recent direct POs to same vendor & category) <= threshold,
                  otherwise needs a CPO-approved exemption
    """
    policy = PurchasingPolicy.get()
    if po.evaluation_id:
        return Sourcing(SourcingBasis.RFQ_AWARD)
    if po.renewal_of_id:
        old, new = po.renewal_of.commitment_value, po.commitment_value
        uplift = ((new - old) / old * 100) if old > 0 else None
        needs = uplift is not None and uplift > policy.renewal_price_tolerance_pct
        notes = [f"Renewal value {uplift:+.1f}% vs {po.renewal_of.po_number} (tolerance {policy.renewal_price_tolerance_pct}%)"] if needs else []
        return Sourcing(SourcingBasis.RENEWAL, needs, notes, uplift_pct=uplift)

    value = po.commitment_value
    since = timezone.now() - timedelta(days=policy.aggregation_window_days)
    recent = (PurchaseOrder.objects.filter(vendor=po.vendor, category=po.category, basis__in=DIRECT_BASES, submitted_at__gte=since)
              .exclude(pk=po.pk).exclude(status__in=[POStatus.DRAFT, POStatus.RETURNED, POStatus.REJECTED, POStatus.CANCELLED]))
    recent_total = sum((o.commitment_value for o in recent), Decimal("0"))
    aggregate = value + recent_total
    if aggregate <= policy.direct_po_threshold:
        return Sourcing(SourcingBasis.DIRECT_BELOW_THRESHOLD, False, [], aggregate, policy.direct_po_threshold)
    if value > policy.direct_po_threshold:
        note = f"Value {value:,.2f} exceeds the direct-purchase threshold {policy.direct_po_threshold:,.2f}"
    else:
        note = (f"Value {value:,.2f} plus {recent_total:,.2f} already ordered from this vendor/category in the last "
                f"{policy.aggregation_window_days} days exceeds the threshold {policy.direct_po_threshold:,.2f}")
    return Sourcing(SourcingBasis.DIRECT_EXEMPTION, True, [note], aggregate, policy.direct_po_threshold)


def validate_for_submission(po) -> None:
    if not po.lines.exists() or po.total_amount <= 0:
        raise WorkflowError("Add at least one line with a value greater than zero.")
    if not po.vendor.is_active:
        raise WorkflowError("The selected vendor is blocked.")
    if po.is_recurring:
        if not (po.frequency and po.start_date and po.end_date):
            raise WorkflowError("A recurring PO needs a frequency, start date and end date.")
        if po.end_date < po.start_date:
            raise WorkflowError("The end date cannot be before the start date.")
        if po.start_date < timezone.localdate():
            raise WorkflowError("The start date has already passed. Move it to today or later.")
    if po.evaluation_id is None and po.renewal_of_id is None and not po.justification.strip():
        raise WorkflowError("Give the business justification for buying without an RFQ.")


# --------------------------------------------------------------------------- approval workflow
@transaction.atomic
def submit_po(po_id, user) -> PurchaseOrder:
    """DRAFT/RETURNED -> IN_APPROVAL: fix the sourcing basis, resolve the PO DoA rule, snapshot the chain."""
    po = _lock(po_id)
    if po.created_by_id != user.pk:
        raise WorkflowError("Only the creator can submit this PO.")
    if po.status not in EDITABLE_STATUSES:
        raise WorkflowError(f"A PO in status {po.get_status_display()} cannot be submitted.")
    recalculate_totals(po)
    validate_for_submission(po)

    src = determine_sourcing(po)
    if src.needs_exemption and not po.exemption_reason.strip():
        raise WorkflowError("This purchase needs a sourcing exemption: " + "; ".join(src.notes) +
                            ". Record the exemption reason (e.g. sole source, emergency) and submit again.")
    rule = DoAResolver.resolve(po.commitment_value, po.category, po.department, doc_type="PO")

    chain = [[s.level, s.approver_role, False] for s in rule.steps.all()]
    if src.needs_exemption:  # the CPO must explicitly grant the exemption
        cpo_levels = [c for c in chain if c[1] == Role.CPO]
        if cpo_levels:
            cpo_levels[-1][2] = True
        else:
            chain.append([max(c[0] for c in chain) + 1, Role.CPO, True])

    cycle = po.current_cycle + 1
    POApprovalStep.objects.bulk_create(
        POApprovalStep(po=po, cycle_no=cycle, level=l, required_role=r, covers_exemption=e) for l, r, e in chain)
    po.basis, po.exemption_required, po.sourcing_notes = src.basis, src.needs_exemption, "; ".join(src.notes)
    po.status, po.current_cycle, po.doa_rule, po.submitted_at = POStatus.IN_APPROVAL, cycle, rule, timezone.now()
    po.save()
    record(user, "PO_SUBMITTED", po, data={"basis": src.basis, "exemption": src.needs_exemption, "rule": rule.name,
                                           "commitment": po.commitment_value, "cycle": cycle})
    return po


@transaction.atomic
def decide(step_id, user, decision, comment="") -> PurchaseOrder:
    if decision not in DECISIONS:
        raise WorkflowError("Unknown decision.")
    comment = (comment or "").strip()
    if decision in (POApprovalStep.Status.REJECTED, POApprovalStep.Status.RETURNED) and not comment:
        raise WorkflowError("A comment is required when rejecting or returning a PO.")
    po_id = POApprovalStep.objects.values_list("po_id", flat=True).get(pk=step_id)
    po = _lock(po_id)
    step = POApprovalStep.objects.get(pk=step_id)
    step.po = po
    blocker = decision_blocker(user, step)
    if blocker:
        raise WorkflowError(blocker)

    step.status, step.decided_by, step.decided_at, step.comment = decision, user, timezone.now(), comment
    step.save()
    pending = po.approval_steps.filter(cycle_no=step.cycle_no, status=POApprovalStep.Status.PENDING)
    if decision == POApprovalStep.Status.APPROVED:
        if not pending.exists():
            po.status, po.approved_at = POStatus.APPROVED, timezone.now()
    else:
        pending.update(status=POApprovalStep.Status.SKIPPED)
        po.status = POStatus.REJECTED if decision == POApprovalStep.Status.REJECTED else POStatus.RETURNED
    po.save()
    record(user, f"PO_{decision}", po, data={"level": step.level, "exemption": step.covers_exemption, "comment": comment})
    return po


# --------------------------------------------------------------------------- lifecycle
@transaction.atomic
def issue_po(po_id, user) -> PurchaseOrder:
    """APPROVED -> ISSUED (one-off) or ACTIVE (recurring agreement). Closes the source PR."""
    po = _lock(po_id)
    if po.status != POStatus.APPROVED:
        raise WorkflowError("Only an approved PO can be issued.")
    if po.is_recurring and po.start_date < timezone.localdate():
        raise WorkflowError("The agreement start date has passed. Cancel and recreate with new dates, or renew.")
    po.status = POStatus.ACTIVE if po.is_recurring else POStatus.ISSUED
    po.issued_at = timezone.now()
    po.save()
    if po.pr_id and po.pr.status in (PRStatus.APPROVED, PRStatus.RFQ_CREATED):
        po.pr.status = PRStatus.CLOSED
        po.pr.save(update_fields=["status", "updated_at"])
    record(user, "PO_ISSUED", po)
    return po


@transaction.atomic
def cancel_po(po_id, user, reason="") -> PurchaseOrder:
    """Creator may cancel before approval completes. After approval, only a Manager/CPO, with a reason."""
    po = _lock(po_id)
    reason = (reason or "").strip()
    if po.status in (POStatus.CANCELLED, POStatus.REJECTED, POStatus.CLOSED):
        raise WorkflowError(f"A PO in status {po.get_status_display()} cannot be cancelled.")
    if po.status in EDITABLE_STATUSES + (POStatus.IN_APPROVAL,):
        if po.created_by_id != user.pk:
            raise WorkflowError("Only the creator can cancel this PO before it is approved.")
    else:
        if user.role not in (Role.MANAGER, Role.CPO):
            raise WorkflowError("Only a Procurement Manager or the CPO can cancel an approved or issued PO.")
        if not reason:
            raise WorkflowError("A reason is required to cancel an approved or issued PO.")
    po.approval_steps.filter(status=POApprovalStep.Status.PENDING).update(status=POApprovalStep.Status.SKIPPED)
    po.status, po.closed_at = POStatus.CANCELLED, timezone.now()
    po.save()
    record(user, "PO_CANCELLED", po, data={"reason": reason})
    return po


@transaction.atomic
def close_po(po_id, user) -> PurchaseOrder:
    """Complete a PO, or end an agreement early (no further releases)."""
    po = _lock(po_id)
    if po.status not in LIVE_STATUSES:
        raise WorkflowError("Only an issued or active PO can be closed.")
    po.status, po.closed_at = POStatus.CLOSED, timezone.now()
    po.save()
    record(user, "PO_CLOSED", po)
    return po


@transaction.atomic
def pause_po(po_id, user) -> PurchaseOrder:
    po = _lock(po_id)
    if not (po.is_recurring and po.status == POStatus.ACTIVE):
        raise WorkflowError("Only an active recurring agreement can be paused.")
    po.status = POStatus.PAUSED
    po.save(update_fields=["status", "updated_at"])
    record(user, "PO_PAUSED", po)
    return po


@transaction.atomic
def resume_po(po_id, user) -> PurchaseOrder:
    """Resume a paused agreement. Occurrences that fell due while paused are SKIPPED, not back-ordered."""
    po = _lock(po_id)
    if po.status != POStatus.PAUSED:
        raise WorkflowError("Only a paused agreement can be resumed.")
    today, skipped = timezone.localdate(), 0
    while True:
        d = occurrence_date(po.start_date, po.end_date, po.frequency, po.schedule_index)
        if d is None or d >= today:
            break
        po.schedule_index += 1
        skipped += 1
    po.status = POStatus.ACTIVE
    po.save()
    record(user, "PO_RESUMED", po, data={"skipped_occurrences": skipped})
    return po


# --------------------------------------------------------------------------- recurring engine
def _release(master: PurchaseOrder, on_date, ordinal: int) -> PurchaseOrder:
    rel = PurchaseOrder.objects.create(
        po_number=next_number("PO"), title=f"{master.title} - release {ordinal}", vendor=master.vendor,
        department=master.department, category=master.category, pr=None, parent=master,
        basis=SourcingBasis.RECURRING_RELEASE, status=POStatus.ISSUED, payment_terms=master.payment_terms,
        charges_amount=master.charges_amount, total_amount=master.total_amount, scheduled_for=on_date,
        delivery_date=on_date, created_by=master.created_by, issued_at=timezone.now(),
        approved_at=master.approved_at, notes=f"Auto-generated under agreement {master.po_number}.")
    POLine.objects.bulk_create(POLine(po=rel, line_no=l.line_no, description=l.description, quantity=l.quantity,
                                      uom=l.uom, unit_price=l.unit_price, line_total=l.line_total) for l in master.lines.all())
    record(None, "PO_RELEASE_GENERATED", rel, data={"agreement": master.po_number, "scheduled_for": on_date})
    return rel


def generate_due_releases(today=None) -> dict:
    """
    Cron entry point (daily). For every ACTIVE agreement, create a pre-authorised release PO for each
    occurrence that is due (date <= today), then close agreements whose schedule is exhausted.
    Safe to run repeatedly: `schedule_index` guarantees each occurrence is generated exactly once.
    """
    today = today or timezone.localdate()
    created = closed = 0
    for master_id in list(PurchaseOrder.objects.filter(is_recurring=True, status=POStatus.ACTIVE).values_list("pk", flat=True)):
        with transaction.atomic():
            master = PurchaseOrder.objects.select_for_update().get(pk=master_id)
            if master.status != POStatus.ACTIVE:
                continue
            for _ in range(MAX_CATCHUP_PER_RUN):
                d = occurrence_date(master.start_date, master.end_date, master.frequency, master.schedule_index)
                if d is None or d > today:
                    break
                _release(master, d, master.schedule_index + 1)
                master.schedule_index += 1
                created += 1
            if occurrence_date(master.start_date, master.end_date, master.frequency, master.schedule_index) is None:
                master.status, master.closed_at = POStatus.CLOSED, timezone.now()
                closed += 1
                record(None, "PO_AGREEMENT_COMPLETED", master)
            master.save()
    return {"releases_created": created, "agreements_closed": closed}
