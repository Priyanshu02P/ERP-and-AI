"""
Builds the side-by-side bid comparison (read-only analysis, no persistence).

Irregularity flags are investigation TRIGGERS, not proof of wrongdoing: a low bid can be
legitimate and a single bid can be justified. Thresholds are configurable in settings.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median

from django.conf import settings
from django.utils import timezone

from apps.documents.services import list_documents
from apps.sourcing.models import Quotation, QuotationStatus


@dataclass
class Flag:
    code: str
    level: str  # "danger" | "warning" | "info"
    text: str


@dataclass
class Row:
    label: str
    kind: str                 # money | text | number | tri | date | docs
    cells: list
    best: list = field(default_factory=list)   # parallel list of bool (highlight)
    group: str = ""

    @property
    def pairs(self):
        return list(zip(self.cells, self.best))


@dataclass
class Comparison:
    rfq: object
    quotes: list
    rows: list
    line_rows: list
    flags: dict            # quotation_id -> [Flag]
    rfq_flags: list
    lowest_landed: object
    estimate: object


def _best(values, pick):
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return [False] * len(values)
    target = pick(present)
    return [v is not None and v == target for v in values]


def current_quotes(rfq):
    """Current revision per vendor (superseded revisions are history, not bids)."""
    return list(Quotation.objects.filter(rfq=rfq).exclude(status=QuotationStatus.SUPERSEDED)
                .select_related("vendor").prefetch_related("lines").order_by("vendor__name"))


def build_comparison(rfq) -> Comparison:
    today = timezone.localdate()
    low_ratio = Decimal(str(settings.LOW_BID_RATIO))
    tolerance = Decimal(str(settings.ABOVE_ESTIMATE_TOLERANCE))
    quotes = current_quotes(rfq)
    rfq_lines = list(rfq.lines.all())
    n_lines = len(rfq_lines)
    estimate = rfq.pr.total_amount

    complete = {q.pk: len(q.lines.all()) >= n_lines for q in quotes}
    comparable = [q for q in quotes if complete[q.pk] and q.status != QuotationStatus.REJECTED]
    lowest = min((q.landed_cost for q in comparable), default=None)
    med = median([q.landed_cost for q in comparable]) if len(comparable) >= 3 else None

    # ---- flags
    flags = {q.pk: [] for q in quotes}
    for q in quotes:
        f = flags[q.pk]
        if q.is_late:
            f.append(Flag("LATE", "warning", "Submitted after the response deadline"))
        if q.valid_until and q.valid_until < today:
            f.append(Flag("EXPIRED", "danger", "Quotation validity has expired"))
        if not complete[q.pk]:
            f.append(Flag("INCOMPLETE", "danger", f"Prices {len(q.lines.all())} of {n_lines} RFQ lines; totals are not comparable"))
        if med is not None and complete[q.pk] and q.landed_cost < med * low_ratio:
            f.append(Flag("LOW_BID", "warning", f"Landed cost is below {int(settings.LOW_BID_RATIO * 100)}% of the median bid; verify scope and pricing"))
        if complete[q.pk] and estimate and q.landed_cost > estimate * (1 + tolerance):
            f.append(Flag("ABOVE_ESTIMATE", "warning", f"More than {int(settings.ABOVE_ESTIMATE_TOLERANCE * 100)}% above the PR estimate"))
        if q.deviations:
            f.append(Flag("DEVIATIONS", "warning", "Supplier takes exceptions to RFQ terms"))
        if None in (q.meets_specs, q.certificates_available, q.capacity_confirmed):
            f.append(Flag("UNCONFIRMED", "info", "Specification / certificate / capacity claims not stated"))
    rfq_flags = []
    live = [q for q in quotes if q.status != QuotationStatus.REJECTED]
    if len(live) == 1:
        rfq_flags.append(Flag("SINGLE_BID", "warning", "Only one live bid: document the justification before award"))
    if not quotes:
        rfq_flags.append(Flag("NO_BIDS", "info", "No quotations recorded yet"))

    # ---- comparison matrix
    def vals(fn):
        return [fn(q) for q in quotes]

    def row(label, kind, fn, group="", best=None):
        cells = vals(fn)
        return Row(label, kind, cells, _best(cells, best) if best else [False] * len(cells), group)

    over_lowest = []
    for q in quotes:
        over_lowest.append(None if lowest in (None, 0) or not complete[q.pk]
                           else round((q.landed_cost - lowest) / lowest * 100, 1))
    docs = {q.pk: list(list_documents("QUOTATION", q.pk)) for q in quotes}

    rows = [
        row("Quoted line total", "money", lambda q: q.total_amount, "Price"),
        row("Discount (deducted)", "money", lambda q: q.discount_amount, "Price"),
        row("Freight and insurance", "money", lambda q: q.freight_insurance, "Price"),
        row("Taxes and duties (non-recoverable)", "money", lambda q: q.taxes_duties, "Price"),
        row("Tooling / setup charges", "money", lambda q: q.setup_charges, "Price"),
        Row("Total landed cost", "money", vals(lambda q: q.landed_cost),
            [complete[q.pk] and q.status != QuotationStatus.REJECTED and q.landed_cost == lowest and len(comparable) > 1 for q in quotes], "Price"),
        Row("% above lowest comparable bid", "pct", over_lowest, [False] * len(quotes), "Price"),
        row("Payment terms", "text", lambda q: q.payment_terms, "Commercial terms"),
        row("Incoterms", "text", lambda q: q.incoterms, "Commercial terms"),
        row("Quoted lead time (days)", "number", lambda q: q.lead_time_days, "Commercial terms", best=min),
        row("Minimum order quantity", "number", lambda q: q.min_order_qty, "Commercial terms"),
        row("Price valid until", "date", lambda q: q.valid_until, "Commercial terms"),
        row("Warranty (months)", "number", lambda q: q.warranty_months, "Commercial terms", best=max),
        row("Meets mandatory specifications", "tri", lambda q: q.meets_specs, "Technical (vendor claims)"),
        row("Required certificates available", "tri", lambda q: q.certificates_available, "Technical (vendor claims)"),
        row("Capacity confirmed", "tri", lambda q: q.capacity_confirmed, "Technical (vendor claims)"),
        row("Exceptions to RFQ terms", "text", lambda q: q.deviations, "Technical (vendor claims)"),
        row("Revision", "number", lambda q: q.revision_no, "Record"),
        Row("Documents", "docs", [docs[q.pk] for q in quotes], [False] * len(quotes), "Record"),
    ]

    # ---- per-line unit prices
    line_rows = []
    for rl in rfq_lines:
        cells = []
        for q in quotes:
            ql = next((x for x in q.lines.all() if x.rfq_line_id == rl.pk), None)
            cells.append(ql)
        prices = [c.unit_price for c in cells if c is not None]
        low = min(prices) if len(prices) > 1 else None
        line_rows.append({"line": rl, "cells": [{"ql": c, "best": c is not None and low is not None and c.unit_price == low} for c in cells]})

    return Comparison(rfq, quotes, rows, line_rows, flags, rfq_flags, lowest, estimate)
