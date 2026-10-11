"""Bid comparison + evaluation: scoring maths, gates, four-eyes review, frozen results, flags, pages."""
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.core.exceptions import WorkflowError
from apps.doa.models import DoARule, DoARuleStep
from apps.evaluation import services
from apps.evaluation.comparison import build_comparison
from apps.evaluation.models import MANUAL_CRITERIA, Evaluation, EvaluationStatus
from apps.masterdata.models import Category, Department, Vendor
from apps.requisitions import services as pr_services
from apps.requisitions.models import PRLine, PurchaseRequisition
from apps.requisitions.policies import approval_inbox
from apps.sourcing import services as rfq_services

PW = "pw-12345-x"


@override_settings(BLOB_BACKEND="local", BLOB_LOCAL_ROOT=tempfile.mkdtemp())
class EvaluationTests(TestCase):
    def setUp(self):
        dept = Department.objects.create(code="IT", name="IT")
        cat = Category.objects.create(code="G", name="Goods")
        rule = DoARule.objects.create(name="all", min_amount=0, max_amount=Decimal("1000000"))
        DoARuleStep.objects.create(rule=rule, level=1, approver_role=Role.MANAGER)
        mk = lambda n, r: User.objects.create_user(n, password=PW, role=r, department=dept)
        self.spec, self.mgr, self.cpo = mk("spec", Role.SPECIALIST), mk("mgr", Role.MANAGER), mk("cpo", Role.CPO)
        self.vendors = [Vendor.objects.create(code=f"V{i}", name=n) for i, n in enumerate(["Alpha", "Bravo", "Charlie"], 1)]
        pr = PurchaseRequisition.objects.create(pr_number="PR-T-1", title="Laptops", department=dept, category=cat, requested_by=self.spec)
        PRLine.objects.create(pr=pr, line_no=1, description="Laptop", quantity=Decimal("5"), est_unit_price=Decimal("1000"))
        pr_services.submit_pr(pr.pk, self.spec)
        pr_services.decide(approval_inbox(self.mgr)[0].pk, self.mgr, "APPROVED")
        self.rfq = rfq_services.create_rfq_from_pr(pr.pk, self.spec, self.vendors, timezone.now() + timedelta(days=2))
        rfq_services.issue_rfq(self.rfq.pk, self.spec)
        self.line = self.rfq.lines.first()

    def quote(self, vendor, unit_price, **extra):
        header = {"quote_date": timezone.localdate(), "valid_until": None, "vendor_ref_no": "", "payment_terms": "",
                  "lead_time_days": 10, "notes": "", "meets_specs": True, "certificates_available": True,
                  "capacity_confirmed": True, **extra}
        row = {"rfq_line_id": self.line.pk, "unit_price": Decimal(unit_price), "quantity": Decimal("5")}
        return rfq_services.record_quotation(self.rfq.pk, vendor.pk, self.spec, header, [row])

    def entry_data(self, entry, score, adjustment="0", note=""):
        return {"gate_specs": True, "gate_certificates": True, "gate_capacity": True, "is_disqualified": False,
                "disqualification_reason": "", "adjustment_amount": Decimal(adjustment), "adjustment_note": note,
                "scores": {c: (Decimal(score), "") for c in MANUAL_CRITERIA}}

    def started(self):
        self.quote(self.vendors[0], "900", freight_insurance=Decimal("500"))   # landed 5000
        self.quote(self.vendors[1], "800")                                      # landed 4000
        rfq_services.close_rfq(self.rfq.pk, self.spec)
        return services.start_evaluation(self.rfq.pk, self.spec)

    # ------------------------------------------------------------------ rules
    def test_cannot_start_until_responses_closed(self):
        self.quote(self.vendors[0], "900")
        with self.assertRaises(WorkflowError):
            services.start_evaluation(self.rfq.pk, self.spec)

    def test_weighted_scoring_cost_adjustment_and_rank(self):
        ev = self.started()
        a, b = ev.entries.order_by("id")
        services.save_evaluation(ev.pk, self.spec, weights=ev.weights, rationale="r", entries={
            a.pk: self.entry_data(a, "80"),                                   # evaluated 5000
            b.pk: self.entry_data(b, "70", "500", "expected defect cost")})   # 4000 + 500 = 4500
        from apps.evaluation.scoring import compute_results
        res = {r.vendor: r for r in compute_results(ev)}
        self.assertEqual(res["Bravo"].cost_score, Decimal("100.00"))
        self.assertEqual(res["Alpha"].cost_score, Decimal("90.00"))
        self.assertEqual(res["Alpha"].weighted_score, Decimal("83.00"))   # 0.3*90 + 0.7*80
        self.assertEqual(res["Bravo"].weighted_score, Decimal("79.00"))   # 0.3*100 + 0.7*70
        self.assertEqual((res["Alpha"].rank, res["Bravo"].rank), (1, 2))  # cheaper bidder does NOT automatically win

    def test_weights_must_total_100(self):
        ev = self.started()
        with self.assertRaises(WorkflowError):
            services.save_evaluation(ev.pk, self.spec, weights={**ev.weights, "COST": 50}, entries={})

    def test_pending_gate_and_missing_rationale_block_submission(self):
        ev = self.started()
        a, b = ev.entries.order_by("id")
        data = {a.pk: self.entry_data(a, "80"), b.pk: self.entry_data(b, "70")}
        data[b.pk]["gate_specs"] = None                                       # not yet assessed
        services.save_evaluation(ev.pk, self.spec, weights=ev.weights, entries=data, rationale="ok")
        with self.assertRaisesMessage(WorkflowError, "Bravo"):
            services.submit_evaluation(ev.pk, self.spec)
        data[b.pk]["gate_specs"] = True
        services.save_evaluation(ev.pk, self.spec, weights=ev.weights, entries=data, rationale="")
        with self.assertRaisesMessage(WorkflowError, "rationale"):
            services.submit_evaluation(ev.pk, self.spec)

    def test_failed_gate_makes_bidder_ineligible(self):
        ev = self.started()
        a, b = ev.entries.order_by("id")
        data = {a.pk: self.entry_data(a, "80"), b.pk: self.entry_data(b, "90")}
        data[b.pk]["gate_certificates"] = False                               # cheapest bidder fails a mandatory gate
        services.save_evaluation(ev.pk, self.spec, weights=ev.weights, entries=data, rationale="Bravo lacks certificates")
        services.submit_evaluation(ev.pk, self.spec)
        ev.refresh_from_db()
        self.assertEqual(ev.recommended_entry_id, a.pk)

    def test_four_eyes_review_and_frozen_snapshot(self):
        ev = self.started()
        a, b = ev.entries.order_by("id")
        services.save_evaluation(ev.pk, self.spec, weights=ev.weights, rationale="Best balance",
                                 entries={a.pk: self.entry_data(a, "80"), b.pk: self.entry_data(b, "70")})
        services.submit_evaluation(ev.pk, self.spec)
        with self.assertRaises(WorkflowError):                                # submitter cannot review own work
            services.review_evaluation(ev.pk, self.spec, services.FINALIZE)
        with self.assertRaises(WorkflowError):                                # return needs a comment
            services.review_evaluation(ev.pk, self.mgr, services.RETURN, "")
        services.review_evaluation(ev.pk, self.mgr, services.FINALIZE)
        ev.refresh_from_db()
        self.assertEqual(ev.status, EvaluationStatus.FINALIZED)
        frozen = [r["weighted_score"] for r in ev.result_snapshot["results"]]
        a.quotation.freight_insurance = Decimal("99999"); a.quotation.save()  # later data change...
        from apps.evaluation.scoring import compute_results
        self.assertNotEqual([str(r.weighted_score) for r in compute_results(ev) if r.weighted_score], frozen)
        ev.refresh_from_db()                                                  # ...does not touch the signed-off result
        self.assertEqual([r["weighted_score"] for r in ev.result_snapshot["results"]], frozen)
        with self.assertRaises(WorkflowError):                                # locked
            services.save_evaluation(ev.pk, self.spec, weights=ev.weights, entries={})

    # ------------------------------------------------------------------ comparison flags
    def test_comparison_flags_and_landed_cost(self):
        self.quote(self.vendors[0], "900", freight_insurance=Decimal("500"), deviations="Net 15 only", certificates_available=None)
        self.quote(self.vendors[1], "880")
        self.quote(self.vendors[2], "300")                                    # far below the median
        cmp = build_comparison(self.rfq)
        by_vendor = {q.vendor.name: {f.code for f in cmp.flags[q.pk]} for q in cmp.quotes}
        self.assertEqual(cmp.lowest_landed, Decimal("1500.00"))
        self.assertIn("LOW_BID", by_vendor["Charlie"])
        self.assertTrue({"DEVIATIONS", "UNCONFIRMED"} <= by_vendor["Alpha"])
        landed = next(r for r in cmp.rows if r.label == "Total landed cost")
        self.assertEqual(landed.cells[0], Decimal("5000.00"))                 # 4500 + 500 freight

    def test_single_bid_flag(self):
        self.quote(self.vendors[0], "900")
        self.assertIn("SINGLE_BID", {f.code for f in build_comparison(self.rfq).rfq_flags})

    # ------------------------------------------------------------------ pages
    def test_pages_render_and_permissions(self):
        ev = self.started()
        c = Client(); c.login(username="spec", password=PW)
        for name in ("evaluation:compare", "evaluation:edit", "evaluation:detail"):
            self.assertEqual(c.get(reverse(name, args=[self.rfq.pk]), follow=True).status_code, 200, name)
        self.assertContains(c.get(reverse("evaluation:compare", args=[self.rfq.pk])), "Total landed cost")
        stranger = User.objects.create_user("stranger", password=PW, role=Role.SPECIALIST)
        c2 = Client(); c2.login(username="stranger", password=PW)
        self.assertEqual(c2.get(reverse("evaluation:compare", args=[self.rfq.pk])).status_code, 403)
        cpo = Client(); cpo.login(username="cpo", password=PW)               # CPO can view but not edit
        self.assertEqual(cpo.get(reverse("evaluation:detail", args=[self.rfq.pk])).status_code, 200)
        self.assertEqual(cpo.get(reverse("evaluation:edit", args=[self.rfq.pk])).status_code, 403)

    def test_edit_form_roundtrip_through_ui(self):
        ev = self.started()
        a, b = ev.entries.order_by("id")
        c = Client(); c.login(username="spec", password=PW)
        post = {"action": "submit", "rationale": "Alpha best overall", "recommended": ""}
        post.update({f"w_{k}": v for k, v in ev.weights.items()})
        for e in (a, b):
            p = f"e{e.pk}-"
            post.update({p + "gate_specs": "true", p + "gate_certificates": "true", p + "gate_capacity": "true",
                         p + "adjustment_amount": "0"})
            post.update({p + f"score_{k}": "75" for k in MANUAL_CRITERIA})
        resp = c.post(reverse("evaluation:edit", args=[self.rfq.pk]), post)
        self.assertEqual(resp.status_code, 302)
        ev.refresh_from_db()
        self.assertEqual(ev.status, EvaluationStatus.UNDER_REVIEW)
        self.assertEqual(ev.recommended_entry_id, b.pk)   # equal manual scores -> cheaper evaluated cost ranks first
