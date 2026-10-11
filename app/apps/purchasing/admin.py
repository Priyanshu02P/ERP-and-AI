from django.contrib import admin

from .models import POLine, PurchaseOrder, PurchasingPolicy


@admin.register(PurchasingPolicy)
class PurchasingPolicyAdmin(admin.ModelAdmin):
    """Singleton: direct-purchase threshold, anti-splitting window, renewal tolerance."""

    def has_add_permission(self, request):
        return not PurchasingPolicy.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class POLineInline(admin.TabularInline):
    model = POLine
    extra = 0
    can_delete = False
    readonly_fields = ("line_no", "description", "quantity", "uom", "unit_price", "line_total")


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    """Read-only: POs change only through the audited workflow."""

    list_display = ("po_number", "title", "vendor", "basis", "status", "total_amount", "is_recurring", "created_by")
    list_filter = ("status", "basis", "is_recurring")
    search_fields = ("po_number", "title", "vendor__name")
    inlines = [POLineInline]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
