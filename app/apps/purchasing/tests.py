"""POs: direct buying rules, exemption, anti-splitting, award, renewal, recurring releases, pages."""
import tempfile
from datetime import date, timedelta
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.core.exceptions import WorkflowError
from apps.doa.models import DoARule, DoARuleStep
from apps.evaluation import services as eval_services
from apps.evaluation.models import MANUAL_CRITERIA
from apps.masterdata.models import Category, Department, Vendor
from apps.purchasing import services
from apps.purchasing.models import POLine, POStatus, PurchaseOrder, PurchasingPolicy, SourcingBasis
from apps.purchasing.policies import approval_inbox
from apps.purchasing.schedule import add_months, occurrence_date, planned_cycles
from apps.requisitions import services as pr_services
from apps.requisitions.models import PRLine, PRStatus, PurchaseRequisition
from apps.requisitions.policies import approval_inbox as pr_inbox
from apps.sourcing import services as rfq_services

PW = "pw-12345-x"


class ScheduleTests(TestCase):
    def test_month_end_does_not_drift(self):
        start = date(2026, 1, 31)
        self.assertEqual(add_months(start, 1), date(2026, 2, 28))
        self.assertEqual(add_months(start, 2), date(2026, 3, 31))      # not 28 Mar
        self.assertEqual(occurrence_date(start, date(2026, 12, 31), "MONTHLY", 3), date(2026, 4, 30))

    def test_cycle_counts(self):
        self.assertEqual(planned_cycles(date(2026, 1, 1), date(2026, 12, 31), "MONTHLY"), 12)
        self.assertEqual(planned_cycles(date(2026, 1, 1), date(2026, 12, 31), "QUARTERLY"), 4)
        self.assertEqual(planned_cycles(date(2026, 1, 1), date(2026, 1, 29), "WEEKLY"), 5)
        self.assertEqual(planned_cycles(None, None, ""), 1)


@override_settings(BLOB_BACKEND="local", BLOB_LOCAL_ROOT=tempfile.mkdtemp())
class PurchaseOrderTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(code="IT", name="IT")
        self.cat = Category.objects.create(code="G", name="Goods")
        for name, hi in (("PO small", "10000"), ("PO large", "1000000")):
            rule = DoARule.objects.create(name=name, doc_type="PO", min_amount=Decimal("0") if hi == "10000" else Decimal("10000.01"),
                                          max_amount=Decimal(hi))
            DoARuleStep.objects.create(rule=rule, level=1, approver_role=Role.MANAGER)
            if hi != "10000":
                DoARuleStep.objects.create(rule=rule, level=2, approver_role=Role.CPO)
        pr_rule = DoARule.objects.create(name="PR all", min_amount=0, max_amount=Decimal("1000000"))
        DoARuleStep.objects.create(rule=pr_rule, level=1, approver_role=Role.MANAGER)
        mk = lambda n, r: User.objects.create_user(n, password=PW, role=r, department=self.dept)
        self.spec, self.mgr, self.cpo = mk("spec", Role.SPECIALIST), mk("mgr", Role.MANAGER), mk("cpo", Role.CPO)
        self.mgr2 = mk("mgr2", Role.MANAGER)
        self.vendors = [Vendor.objects.create(code=f"V{i}", name=n) for i, n in enumerate(["Alpha", "Bravo", "Charlie"], 1)]
        self.policy = PurchasingPolicy.get()      # threshold 5000, window 30d, renewal tolerance 5%

    # ------------------------------------------------------------------ helpers
    def make_po(self, total="1000", vendor=None, user=None, justification="Urgent spares", **extra):
        po = PurchaseOrder.objects.create(po_number=f"PO-T-{PurchaseOrder.objects.count() + 1}", title="Spares",
                                          vendor=vendor or self.vendors[0], department=self.dept, category=self.cat,
                                          created_by=user or self.spec, justification=justification, **extra)
        POLine.objects.create(po=po, line_no=1, description="Item", quantity=Decimal("1"), unit_price=Decimal(total))
        services.recalculate_totals(po)
        return po

    def approve_all(self, po):
        while True:
            step = next(iter(approval_inbox(self.mgr) or approval_inbox(self.cpo) or approval_inbox(self.mgr2)), None)
            if step is None:
                break
            actor = {Role.MANAGER: self.mgr, Role.CPO: self.cpo}[step.required_role]
            services.decide(step.pk, actor, "APPROVED")
        po.refresh_from_db()
        return po

    # ------------------------------------------------------------------ direct buying
    def test_below_threshold_needs_no_rfq_or_exemption(self):
        po = self.make_po("4000")
        services.submit_po(po.pk, self.spec)
        po.refresh_from_db()
        self.assertEqual((po.basis, po.exemption_required), (SourcingBasis.DIRECT_BELOW_THRESHOLD, False))
        self.assertEqual(list(po.approval_steps.values_list("required_role", flat=True)), [Role.MANAGER])
        self.approve_all(po)
        self.assertEqual(po.status, POStatus.APPROVED)
        services.issue_po(po.pk, self.spec)
        po.refresh_from_db()
        self.assertEqual(po.status, POStatus.ISSUED)

    def test_justification_required_for_direct_po(self):
        po = self.make_po("1000", justification="")
        with self.assertRaisesMessage(WorkflowError, "justification"):
            services.submit_po(po.pk, self.spec)

    def test_above_threshold_needs_exemption_reason_then_cpo_step(self):
        po = self.make_po("8000")
        with self.assertRaisesMessage(WorkflowError, "exemption"):
            services.submit_po(po.pk, self.spec)
        po.exemption_reason = "Sole manufacturer of this part"
        po.save()
        services.submit_po(po.pk, self.spec)
        po.refresh_from_db()
        self.assertEqual(po.basis, SourcingBasis.DIRECT_EXEMPTION)
        steps = list(po.approval_steps.order_by("level"))
        self.assertEqual([(s.required_role, s.covers_exemption) for s in steps], [(Role.MANAGER, False), (Role.CPO, True)])
        self.approve_all(po)
        self.assertEqual(po.status, POStatus.APPROVED)

    def test_exemption_marks_existing_cpo_level_when_chain_already_has_one(self):
        po = self.make_po("20000", exemption_reason="Emergency repair")     # 10k-1M rule is Manager -> CPO
        services.submit_po(po.pk, self.spec)
        steps = list(po.approval_steps.order_by("level"))
        self.assertEqual(len(steps), 2)                                      # no extra step added
        self.assertTrue(steps[1].covers_exemption)

    def test_splitting_a_purchase_is_caught_by_aggregation(self):
        first = self.make_po("3000")
        services.submit_po(first.pk, self.spec)
        second = self.make_po("3000")                                        # same vendor + category within the window
        with self.assertRaisesMessage(WorkflowError, "already ordered"):
            services.submit_po(second.pk, self.spec)
        other_vendor = self.make_po("3000", vendor=self.vendors[1])          # different vendor: independent
        services.submit_po(other_vendor.pk, self.spec)
        other_vendor.refresh_from_db()
        self.assertEqual(other_vendor.basis, SourcingBasis.DIRECT_BELOW_THRESHOLD)

    def test_creator_cannot_approve_own_po_and_cpo_cannot_skip_manager(self):
        po = self.make_po("2000", user=self.mgr)
        services.submit_po(po.pk, self.mgr)
        self.assertEqual(approval_inbox(self.mgr), [])                       # own PO
        step = po.approval_steps.get()
        with self.assertRaises(WorkflowError):
            services.decide(step.pk, self.cpo, "APPROVED")                   # wrong role for the step
        services.decide(step.pk, self.mgr2, "APPROVED")

    def test_cancel_rules(self):
        po = self.make_po("1000")
        with self.assertRaises(WorkflowError):
            services.cancel_po(po.pk, self.mgr)                              # not the creator, not yet approved
        services.submit_po(po.pk, self.spec)
        self.approve_all(po)
        with self.assertRaises(WorkflowError):
            services.cancel_po(po.pk, self.spec)                             # approved: only Manager/CPO
        with self.assertRaisesMessage(WorkflowError, "reason"):
            services.cancel_po(po.pk, self.mgr, "")
        services.cancel_po(po.pk, self.mgr, "Vendor withdrew")
        po.refresh_from_db()
        self.assertEqual(po.status, POStatus.CANCELLED)

    # ------------------------------------------------------------------ award from evaluation
    def finalized_evaluation(self):
        pr = PurchaseRequisition.objects.create(pr_number="PR-T-1", title="Laptops", department=self.dept, category=self.cat, requested_by=self.spec)
        PRLine.objects.create(pr=pr, line_no=1, description="Laptop", quantity=Decimal("5"), est_unit_price=Decimal("1000"))
        pr_services.submit_pr(pr.pk, self.spec)
        pr_services.decide(pr_inbox(self.mgr)[0].pk, self.mgr, "APPROVED")
        rfq = rfq_services.create_rfq_from_pr(pr.pk, self.spec, self.vendors[:2], timezone.now() + timedelta(days=2))
        rfq_services.issue_rfq(rfq.pk, self.spec)
        line = rfq.lines.first()
        header = {"quote_date": timezone.localdate(), "valid_until": None, "vendor_ref_no": "", "payment_terms": "Net 45",
                  "lead_time_days": 10, "notes": "", "meets_specs": True, "certificates_available": True, "capacity_confirmed": True}
        rfq_services.record_quotation(rfq.pk, self.vendors[0].pk, self.spec, {**header, "freight_insurance": Decimal("300"), "discount_amount": Decimal("100")},
                                      [{"rfq_line_id": line.pk, "unit_price": Decimal("900"), "quantity": Decimal("5")}])
        rfq_services.record_quotation(rfq.pk, self.vendors[1].pk, self.spec, header,
                                      [{"rfq_line_id": line.pk, "unit_price": Decimal("950"), "quantity": Decimal("5")}])
        rfq_services.close_rfq(rfq.pk, self.spec)
        ev = eval_services.start_evaluation(rfq.pk, self.spec)
        entry_data = {"gate_specs": True, "gate_certificates": True, "gate_capacity": True, "is_disqualified": False,
                      "disqualification_reason": "", "adjustment_amount": Decimal("0"), "adjustment_note": "",
                      "scores": {c: (Decimal("80"), "") for c in MANUAL_CRITERIA}}
        eval_services.save_evaluation(ev.pk, self.spec, weights=ev.weights, rationale="Best value",
                                      entries={e.pk: entry_data for e in ev.entries.all()})
        eval_services.submit_evaluation(ev.pk, self.spec)
        eval_services.review_evaluation(ev.pk, self.mgr, eval_services.FINALIZE)
        ev.refresh_from_db()
        return ev, pr

    def test_award_po_copies_quote_and_closes_pr_on_issue(self):
        ev, pr = self.finalized_evaluation()
        c = Client(); c.login(username="spec", password=PW)
        self.assertContains(c.get(reverse("evaluation:detail", args=[ev.rfq_id])), "Create purchase order")
        po = services.create_po_from_evaluation(ev.pk, self.spec)
        self.assertEqual((po.vendor, po.basis, po.pr_id), (self.vendors[0], SourcingBasis.RFQ_AWARD, pr.pk))
        self.assertEqual(po.total_amount, Decimal("4700.00"))                 # 4500 lines + 300 freight - 100 discount
        self.assertEqual(po.payment_terms, "Net 45")
        with self.assertRaisesMessage(WorkflowError, "already exists"):
            services.create_po_from_evaluation(ev.pk, self.spec)
        services.submit_po(po.pk, self.spec)                                  # award approval = PO approval
        po.refresh_from_db()
        self.assertEqual(po.basis, SourcingBasis.RFQ_AWARD)
        self.assertFalse(po.exemption_required)                               # competitive: no exemption, no justification needed
        self.approve_all(po)
        services.issue_po(po.pk, self.spec)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PRStatus.CLOSED)

    # ------------------------------------------------------------------ renewals
    def issued_po(self, total="4000", **extra):
        po = self.make_po(total, **extra)
        services.submit_po(po.pk, self.spec)
        self.approve_all(po)
        services.issue_po(po.pk, self.spec)
        po.refresh_from_db()
        return po

    def test_renewal_is_rfq_exempt_within_tolerance_and_exemption_beyond(self):
        old = self.issued_po("9000", exemption_reason="Sole source")          # above threshold, approved earlier
        new = services.renew_po(old.pk, self.spec)
        self.assertEqual((new.basis, new.renewal_of_id), (SourcingBasis.RENEWAL, old.pk))
        services.submit_po(new.pk, self.spec)                                 # same price: no RFQ, no exemption
        new.refresh_from_db()
        self.assertFalse(new.exemption_required)
        with self.assertRaisesMessage(WorkflowError, "already has a renewal"):
            services.renew_po(old.pk, self.spec)

        old2 = self.issued_po("4000", vendor=self.vendors[2])
        renewal = services.renew_po(old2.pk, self.spec)
        line = renewal.lines.get(); line.unit_price = Decimal("4400"); line.save()      # +10% > 5% tolerance
        with self.assertRaisesMessage(WorkflowError, "exemption"):
            services.submit_po(renewal.pk, self.spec)
        renewal.exemption_reason = "Supplier price rise; market checked"
        renewal.save()
        services.submit_po(renewal.pk, self.spec)
        renewal.refresh_from_db()
        self.assertTrue(renewal.exemption_required)
        self.assertTrue(renewal.approval_steps.filter(covers_exemption=True, required_role=Role.CPO).exists())

    # ------------------------------------------------------------------ recurring
    def recurring_master(self, monthly="1000", months=3):
        start = timezone.localdate() + timedelta(days=1)
        end = add_months(start, months) - timedelta(days=1)
        po = self.make_po(monthly, is_recurring=True, frequency="MONTHLY", start_date=start, end_date=end)
        return po

    def test_recurring_commitment_drives_threshold_and_doa(self):
        po = self.recurring_master("1000", 12)                                # 1,000/month looks small but commits 12,000
        self.assertEqual((po.cycles, po.commitment_value), (12, Decimal("12000.00")))
        with self.assertRaisesMessage(WorkflowError, "exemption"):
            services.submit_po(po.pk, self.spec)
        po.exemption_reason = "Annual service contract, sole provider"
        po.save()
        services.submit_po(po.pk, self.spec)
        self.assertEqual(po.approval_steps.count(), 2)                        # 12,000 falls in the Manager -> CPO rule

    def test_recurring_agreement_generates_releases_exactly_once_and_completes(self):
        po = self.recurring_master("1000", 3)
        po.justification = "Monthly cleaning"; po.exemption_reason = "n/a"; po.save()
        services.submit_po(po.pk, self.spec)
        self.approve_all(po)
        services.issue_po(po.pk, self.spec)
        po.refresh_from_db()
        self.assertEqual(po.status, POStatus.ACTIVE)

        self.assertEqual(services.generate_due_releases(today=po.start_date - timedelta(days=1))["releases_created"], 0)
        self.assertEqual(services.generate_due_releases(today=po.start_date)["releases_created"], 1)
        self.assertEqual(services.generate_due_releases(today=po.start_date)["releases_created"], 0)     # idempotent
        late = add_months(po.start_date, 5)                                    # catch-up of the remaining two
        res = services.generate_due_releases(today=late)
        self.assertEqual(res, {"releases_created": 2, "agreements_closed": 1})
        po.refresh_from_db()
        self.assertEqual((po.status, po.releases.count()), (POStatus.CLOSED, 3))
        rel = po.releases.order_by("scheduled_for").first()
        self.assertEqual((rel.basis, rel.status, rel.total_amount, rel.lines.count()), (SourcingBasis.RECURRING_RELEASE, POStatus.ISSUED, Decimal("1000.00"), 1))

    def test_pause_skips_missed_occurrences_on_resume(self):
        po = self.recurring_master("500", 6)
        po.justification = "x"; po.exemption_reason = "y"; po.save()
        services.submit_po(po.pk, self.spec)
        self.approve_all(po)
        services.issue_po(po.pk, self.spec)
        services.pause_po(po.pk, self.spec)
        self.assertEqual(services.generate_due_releases(today=add_months(po.start_date, 2))["releases_created"], 0)
        # simulate resume "today" being two months later by moving the agreement start back
        PurchaseOrder.objects.filter(pk=po.pk).update(start_date=timezone.localdate() - timedelta(days=75),
                                                      end_date=timezone.localdate() + timedelta(days=200))
        services.resume_po(po.pk, self.spec)
        po.refresh_from_db()
        self.assertEqual(po.status, POStatus.ACTIVE)
        self.assertGreaterEqual(po.schedule_index, 2)                         # missed months skipped, not back-ordered
        self.assertEqual(po.releases.count(), 0)

    # ------------------------------------------------------------------ pages / cron
    def test_pages_and_cron_endpoint(self):
        po = self.issued_po("1500")
        c = Client(); c.login(username="spec", password=PW)
        for url in (reverse("purchasing:po_list"), reverse("purchasing:po_list") + "?kind=recurring", reverse("purchasing:po_create"),
                    reverse("purchasing:po_detail", args=[po.pk]), reverse("purchasing:po_print", args=[po.pk]),
                    reverse("purchasing:renewals")):
            self.assertEqual(c.get(url).status_code, 200, url)
        self.assertContains(c.get(reverse("purchasing:po_detail", args=[po.pk])), "Renew")
        other = Client(); other.login(username="cpo", password=PW)
        self.assertEqual(other.get(reverse("purchasing:po_detail", args=[po.pk])).status_code, 200)
        self.assertEqual(c.get(reverse("purchasing:inbox")).status_code, 403)   # specialists have no approvals
        with self.settings(CRON_SECRET="s3cret"):
            self.assertEqual(Client().get("/internal/cron/recurring-pos").status_code, 403)
            ok = Client().get("/internal/cron/recurring-pos", HTTP_AUTHORIZATION="Bearer s3cret")
            self.assertEqual((ok.status_code, ok.json()["releases_created"]), (200, 0))

    def test_detail_pages_show_preview_and_schedule(self):
        c = Client(); c.login(username="spec", password=PW)
        draft = self.make_po("8000")
        html = c.get(reverse("purchasing:po_detail", args=[draft.pk])).content.decode()
        self.assertIn("If submitted now", html)
        self.assertIn("needs a CPO-approved sourcing exemption", html)
        master = self.recurring_master("1000", 3)
        self.assertContains(c.get(reverse("purchasing:po_detail", args=[master.pk])), "Whole-term commitment (3 releases)")
        renewals = c.get(reverse("purchasing:renewals"))
        self.assertEqual(renewals.status_code, 200)

    def test_create_form_roundtrip_and_prefill_from_pr(self):
        pr = PurchaseRequisition.objects.create(pr_number="PR-T-9", title="Cables", department=self.dept, category=self.cat,
                                                requested_by=self.spec, status=PRStatus.APPROVED)
        PRLine.objects.create(pr=pr, line_no=1, description="HDMI cable", quantity=Decimal("20"), est_unit_price=Decimal("12"))
        c = Client(); c.login(username="spec", password=PW)
        page = c.get(reverse("purchasing:po_create") + f"?pr={pr.pk}")
        self.assertContains(page, "HDMI cable")
        data = {"title": "Cables", "vendor": self.vendors[0].pk, "department": self.dept.pk, "category": self.cat.pk, "pr": pr.pk,
                "justification": "Small consumable buy", "charges_amount": "0", "exemption_reason": "", "payment_terms": "",
                "lines-TOTAL_FORMS": 1, "lines-INITIAL_FORMS": 0, "lines-MIN_NUM_FORMS": 1, "lines-MAX_NUM_FORMS": 1000,
                "lines-0-description": "HDMI cable", "lines-0-quantity": "20", "lines-0-uom": "EA", "lines-0-unit_price": "12"}
        resp = c.post(reverse("purchasing:po_create"), data)
        self.assertEqual(resp.status_code, 302)
        po = PurchaseOrder.objects.get(title="Cables")
        self.assertEqual(po.total_amount, Decimal("240.00"))
        self.assertEqual(c.post(reverse("purchasing:po_submit", args=[po.pk])).status_code, 302)
        po.refresh_from_db()
        self.assertEqual(po.status, POStatus.IN_APPROVAL)
