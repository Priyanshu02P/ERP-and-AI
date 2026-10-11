"""
Pure scoring logic (no HTTP, no writes) so it is easy to test and reuse.

Order of evaluation:
  1. Mandatory gates      -> eligible / ineligible / pending (not yet assessed)
  2. Evaluated cost       -> landed cost + documented adjustments
  3. Cost score           -> 100 x (lowest eligible evaluated cost / this bidder's evaluated cost)
  4. Weighted score       -> sum(weight% x score) over all six criteria (eligible bidders only)
Missing manual scores leave the weighted score empty: unknown is never treated as zero risk.
"""
import json
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from .models import MANUAL_CRITERIA, Criterion

CENT = Decimal("0.01")
GATES = [
    ("gate_specs", "Meets mandatory specifications"),
    ("gate_certificates", "Required certificates available"),
    ("gate_capacity", "Capacity confirmed for required quantity"),
]
CRITERIA_ORDER = [Criterion.COST.value] + MANUAL_CRITERIA


@dataclass
class EntryResult:
    entry_id: int
    quotation_id: int
    quote_number: str
    vendor: str
    eligible: bool
    reasons: list = field(default_factory=list)        # why ineligible
    pending_gates: list = field(default_factory=list)  # gates not yet assessed
    landed_cost: Decimal = Decimal("0")
    adjustment: Decimal = Decimal("0")
    evaluated_cost: Decimal = Decimal("0")
    cost_score: Optional[Decimal] = None
    manual: dict = field(default_factory=dict)          # criterion -> Decimal | None
    missing: list = field(default_factory=list)         # manual criteria without a score
    weighted_score: Optional[Decimal] = None
    rank: Optional[int] = None


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def assess_gates(entry, rfq_line_count: int, today):
    """Return (ineligibility reasons, pending gate labels) for one entry."""
    if entry.is_disqualified:
        return [f"Disqualified: {entry.disqualification_reason or 'no reason recorded'}"], []
    reasons, pending = [], []
    for attr, label in GATES:
        value = getattr(entry, attr)
        if value is None:
            pending.append(label)
        elif value is False:
            reasons.append(f"Fails: {label}")
    quote = entry.quotation
    if len(quote.lines.all()) < rfq_line_count:
        reasons.append("Quotation does not price every RFQ line")
    if quote.valid_until and quote.valid_until < today:
        reasons.append("Quotation validity has expired")
    if quote.landed_cost + entry.adjustment_amount <= 0:
        reasons.append("Evaluated cost is not positive")
    return reasons, pending


def compute_results(evaluation, today=None):
    """Compute ranked results for an evaluation from its current saved state."""
    today = today or timezone.localdate()
    weights = {k: Decimal(str(v)) for k, v in evaluation.weights.items()}
    line_count = evaluation.rfq.lines.count()
    entries = list(evaluation.entries.select_related("quotation__vendor").prefetch_related("quotation__lines", "scores"))

    results = []
    for e in entries:
        reasons, pending = assess_gates(e, line_count, today)
        landed = e.quotation.landed_cost
        manual = {s.criterion: s.score for s in e.scores.all()}
        results.append(EntryResult(
            entry_id=e.pk, quotation_id=e.quotation_id, quote_number=e.quotation.quote_number,
            vendor=e.quotation.vendor.name, eligible=not reasons and not pending, reasons=reasons,
            pending_gates=pending, landed_cost=landed, adjustment=e.adjustment_amount,
            evaluated_cost=landed + e.adjustment_amount,
            manual={c: manual.get(c) for c in MANUAL_CRITERIA},
            missing=[c for c in MANUAL_CRITERIA if manual.get(c) is None],
        ))

    eligible = [r for r in results if r.eligible]
    if eligible:
        lowest = min(r.evaluated_cost for r in eligible)
        for r in eligible:
            r.cost_score = _q(Decimal(100) * lowest / r.evaluated_cost)
            if not r.missing:
                total = weights[Criterion.COST] / 100 * r.cost_score
                total += sum(weights[c] / 100 * r.manual[c] for c in MANUAL_CRITERIA)
                r.weighted_score = _q(total)
        ranked = sorted((r for r in eligible if r.weighted_score is not None),
                        key=lambda r: (-r.weighted_score, r.evaluated_cost))
        for i, r in enumerate(ranked, start=1):
            r.rank = i
    return results


def results_to_dicts(results):
    """JSON-safe dicts (Decimals -> strings) for templates and the finalised snapshot."""
    rows = []
    for r in results:
        d = json.loads(json.dumps(asdict(r), cls=DjangoJSONEncoder))
        d["score_list"] = [d["cost_score"]] + [d["manual"].get(c) for c in MANUAL_CRITERIA]
        rows.append(d)
    return sorted(rows, key=lambda d: (d["rank"] is None, d["rank"] or 0, d["vendor"]))
