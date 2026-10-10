from django.contrib import admin

from .models import PRApprovalStep, PRLine, PurchaseRequisition


class LineInline(admin.TabularInline):
    model = PRLine
    extra = 0


class StepInline(admin.TabularInline):
    model = PRApprovalStep
    extra = 0
    readonly_fields = ("cycle_no", "level", "required_role", "status", "decided_by", "decided_at", "comment")
    can_delete = False


@admin.register(PurchaseRequisition)
class PRAdmin(admin.ModelAdmin):
    """Read-mostly: workflow state must change through the services, not the admin."""

    list_display = ("pr_number", "title", "department", "total_amount", "status", "requested_by")
    list_filter = ("status", "department", "category")
    search_fields = ("pr_number", "title")
    inlines = [LineInline, StepInline]
    readonly_fields = ("pr_number", "status", "total_amount", "requested_by", "doa_rule", "current_cycle",
                       "submitted_at", "approved_at")
