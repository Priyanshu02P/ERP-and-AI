"""End-to-end: PR -> DoA approvals -> RFQ -> quotation (DB + file) with permission checks."""
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.audit.models import AuditLog
from apps.core.exceptions import WorkflowError
from apps.doa.models import DoARule, DoARuleStep
from apps.documents.models import Document
from apps.masterdata.models import Category, Department, Vendor
from apps.requisitions import services as pr_services
from apps.requisitions.models import PRLine, PRStatus, PurchaseRequisition
from apps.requisitions.policies import approval_inbox
from apps.sourcing import services
from apps.sourcing.models import QuotationStatus, RFQStatus


@override_settings(BLOB_BACKEND="local", BLOB_LOCAL_ROOT=tempfile.mkdtemp())
class ProcurementFlowTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(code="IT", name="IT")
        self.cat = Category.objects.create(code="G", name="Goods")
        rule = DoARule.objects.create(name="10k-100k", min_amount=0, max_amount=Decimal("100000"))
        DoARuleStep.objects.create(rule=rule, level=1, approver_role=Role.MANAGER)
        DoARuleStep.objects.create(rule=rule, level=2, approver_role=Role.CPO)
        mk = lambda n, r: User.objects.create_user(n, password="pw-12345-x", role=r, department=self.dept)
        self.spec, self.mgr, self.cpo = mk("spec", Role.SPECIALIST), mk("mgr", Role.MANAGER), mk("cpo", Role.CPO)
        self.vendors = [Vendor.objects.create(code=f"V{i}", name=f"Vendor {i}") for i in (1, 2)]

    def make_pr(self, price="1000"):
        pr = PurchaseRequisition.objects.create(
            pr_number="PR-T-%d" % (PurchaseRequisition.objects.count() + 1), title="Laptops",
            department=self.dept, category=self.cat, requested_by=self.spec)
        PRLine.objects.create(pr=pr, line_no=1, description="Laptop", quantity=Decimal("5"), est_unit_price=Decimal(price))
        return pr

    def approved_pr(self):
        pr = self.make_pr()
        pr_services.submit_pr(pr.pk, self.spec)
        for user in (self.mgr, self.cpo):
            step = approval_inbox(user)[0]
            pr_services.decide(step.pk, user, "APPROVED")
        pr.refresh_from_db()
        return pr

    def test_dual_approval_chain_and_controls(self):
        pr = self.make_pr()
        pr_services.submit_pr(pr.pk, self.spec)
        pr.refresh_from_db()
        self.assertEqual((pr.status, pr.total_amount), (PRStatus.IN_APPROVAL, Decimal("5000.00")))
        self.assertEqual(approval_inbox(self.cpo), [])           # level 1 not done yet
        self.assertEqual(approval_inbox(self.spec), [])          # wrong role
        step1 = approval_inbox(self.mgr)[0]
        with self.assertRaises(WorkflowError):                   # reject needs a comment
            pr_services.decide(step1.pk, self.mgr, "REJECTED", "")
        pr_services.decide(step1.pk, self.mgr, "APPROVED")
        pr_services.decide(approval_inbox(self.cpo)[0].pk, self.cpo, "APPROVED")
        pr.refresh_from_db()
        self.assertEqual(pr.status, PRStatus.APPROVED)

    def test_self_approval_blocked(self):
        pr = self.make_pr()
        pr.requested_by = self.mgr
        pr.save()
        pr_services.submit_pr(pr.pk, self.mgr)
        self.assertEqual(approval_inbox(self.mgr), [])

    def test_return_then_resubmit_creates_new_cycle(self):
        pr = self.make_pr()
        pr_services.submit_pr(pr.pk, self.spec)
        pr_services.decide(approval_inbox(self.mgr)[0].pk, self.mgr, "RETURNED", "fix specs")
        pr.refresh_from_db()
        self.assertEqual(pr.status, PRStatus.RETURNED)
        pr_services.submit_pr(pr.pk, self.spec)
        pr.refresh_from_db()
        self.assertEqual(pr.current_cycle, 2)
        self.assertEqual(pr.approval_steps.count(), 4)           # history kept

    def test_rfq_requires_approved_pr(self):
        pr = self.make_pr()
        with self.assertRaises(WorkflowError):
            services.create_rfq_from_pr(pr.pk, self.spec, self.vendors, timezone.now() + timedelta(days=3))

    def test_rfq_to_quotation_with_document(self):
        pr = self.approved_pr()
        deadline = timezone.now() + timedelta(days=3)
        rfq = services.create_rfq_from_pr(pr.pk, self.spec, self.vendors, deadline)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PRStatus.RFQ_CREATED)
        with self.assertRaises(WorkflowError):                   # not issued yet
            services.record_quotation(rfq.pk, self.vendors[0].pk, self.spec, {"quote_date": timezone.localdate()}, [])
        services.issue_rfq(rfq.pk, self.spec)

        line = rfq.lines.first()
        row = {"rfq_line_id": line.pk, "unit_price": Decimal("900"), "quantity": Decimal("5"), "delivery_days": 7, "remarks": ""}
        header = {"quote_date": timezone.localdate(), "valid_until": None, "vendor_ref_no": "Q-1",
                  "payment_terms": "Net 30", "lead_time_days": 7, "notes": ""}
        pdf = SimpleUploadedFile("quote.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        q1 = services.record_quotation(rfq.pk, self.vendors[0].pk, self.spec, header, [row], upload=pdf)
        self.assertEqual((q1.total_amount, q1.revision_no, q1.is_late), (Decimal("4500.00"), 1, False))
        doc = Document.objects.get(owner_type="QUOTATION", owner_id=q1.pk)
        self.assertEqual(len(doc.sha256), 64)

        q2 = services.record_quotation(rfq.pk, self.vendors[0].pk, self.spec, header, [{**row, "unit_price": Decimal("850")}])
        q1.refresh_from_db()
        self.assertEqual((q2.revision_no, q1.status), (2, QuotationStatus.SUPERSEDED))

        # download: permitted for the specialist, blocked for an unrelated specialist
        client = Client()
        client.login(username="spec", password="pw-12345-x")
        resp = client.get(reverse("documents:download", args=[doc.pk]))
        self.assertEqual(b"".join(resp.streaming_content), b"%PDF-1.4 test")
        other = User.objects.create_user("other", password="pw-12345-x", role=Role.SPECIALIST)
        client2 = Client()
        client2.login(username="other", password="pw-12345-x")
        self.assertEqual(client2.get(reverse("documents:download", args=[doc.pk])).status_code, 403)

        services.close_rfq(rfq.pk, self.spec)
        rfq.refresh_from_db()
        self.assertEqual(rfq.status, RFQStatus.RESPONSES_CLOSED)
        self.assertTrue(AuditLog.objects.filter(action="QUOTATION_RECORDED").exists())

    def test_uninvited_vendor_rejected(self):
        pr = self.approved_pr()
        rfq = services.create_rfq_from_pr(pr.pk, self.spec, self.vendors[:1], timezone.now() + timedelta(days=1))
        services.issue_rfq(rfq.pk, self.spec)
        row = {"rfq_line_id": rfq.lines.first().pk, "unit_price": Decimal("1"), "quantity": Decimal("5")}
        with self.assertRaises(WorkflowError):
            services.record_quotation(rfq.pk, self.vendors[1].pk, self.spec, {"quote_date": timezone.localdate()}, [row])


class AdminAuthTests(TestCase):
    def test_hardcoded_admin_login_and_role_management(self):
        c = Client()
        self.assertFalse(c.login(username="admin", password="wrong"))
        self.assertTrue(c.login(username="admin", password="Admin@12345"))
        admin = User.objects.get(username="admin")
        self.assertEqual(admin.role, Role.ADMIN)
        self.assertFalse(admin.has_usable_password())

        resp = c.post(reverse("accounts:user_create"), {
            "username": "newuser", "role": Role.MANAGER, "is_active": "on",
            "password1": "Str0ng-pass-99", "password2": "Str0ng-pass-99"})
        self.assertEqual(resp.status_code, 302)
        u = User.objects.get(username="newuser")
        self.assertEqual(u.role, Role.MANAGER)
        c.post(reverse("accounts:user_update", args=[u.pk]), {
            "username": "newuser", "role": Role.CPO, "is_active": "on"})
        u.refresh_from_db()
        self.assertEqual(u.role, Role.CPO)

        # the reserved username cannot be taken, and non-admins cannot reach user management
        resp = c.post(reverse("accounts:user_create"), {
            "username": "Admin", "role": Role.CPO, "password1": "Str0ng-pass-99", "password2": "Str0ng-pass-99"})
        self.assertEqual(resp.status_code, 200)
        c2 = Client()
        c2.login(username="newuser", password="Str0ng-pass-99")
        self.assertEqual(c2.get(reverse("accounts:user_list")).status_code, 403)
